import "frida-il2cpp-bridge";
import { installCaptureHook, resolveTypedNames } from "./lib/mx";

// Production capture + on-demand refresh.
//  - installs the JSON-deserialize hook (captures every MX packet as plaintext)
//  - rpc.refresh(taskNames): QUEUES *NetworkTask(s). They are dispatched from
//    inside the capture hook (the game's own network-processing context, on the
//    main thread), because Unity native calls (AddComponent/get_gameObject) only
//    work there. The drain runs only while something is queued -> no per-frame cost.
//  - rpc.listTasks(): enumerate available *NetworkTask class names.

const MGR_CLASS = "Assets._MX.Program.Scripts.Network.NetworkTaskManager";
const seen = new Set<string>();

const pending: { task: string; attempts: number }[] = [];
const pendingDestroy: Il2Cpp.Object[] = [];
const MAX_ATTEMPTS = 20;
let draining = false;
let firstError = true;

function drain() {
    if (draining || pending.length === 0) return;
    draining = true;
    try {
        const item = pending[0];
        try {
            fireNow(item.task);
            pending.shift();
            send({ type: "fired", task: item.task });
        } catch (e: any) {
            item.attempts++;
            if (firstError) { firstError = false; send({ type: "dbg", task: item.task, step: "fire-failed", extra: e && e.message ? e.message : String(e) }); }
            if (item.attempts >= MAX_ATTEMPTS) {
                pending.shift();
                send({ type: "fire_error", task: item.task, error: (e && e.message ? e.message : String(e)) + ` (after ${item.attempts} attempts)` });
            }
        }
    } finally {
        draining = false;
    }
}

function fireNow(taskName: string) {
    const BA = Il2Cpp.domain.assembly("BlueArchive").image;
    const core = Il2Cpp.domain.assembly("UnityEngine.CoreModule").image;
    const mgrs = Il2Cpp.gc.choose(BA.class(MGR_CLASS));
    if (mgrs.length === 0) throw new Error("no live NetworkTaskManager");
    const mgr = mgrs[0];
    // Each fire gets its own throwaway GameObject so repeated refreshes never
    // collide (NetworkTasks are [DisallowMultipleComponent]). Destroyed after 15s.
    const go = core.class("UnityEngine.GameObject").new();
    const taskClass = BA.class(taskName);
    const task = go.method<Il2Cpp.Object>("AddComponent", 1).invoke(taskClass.type.object);
    mgr.method("Request").invoke(task, NULL);
    setTimeout(() => { pendingDestroy.push(go); }, 15000);
}

function drainDestroy() {
    if (pendingDestroy.length === 0) return;
    try {
        const UO = Il2Cpp.domain.assembly("UnityEngine.CoreModule").image.class("UnityEngine.Object");
        while (pendingDestroy.length) {
            const t = pendingDestroy.shift()!;
            try { UO.method("Destroy", 1).invoke(t); } catch (e) { }
        }
    } catch (e) { }
}

// Reliable per-frame main-thread pump: GameMain.Update is the top-level game
// loop — always active, once per frame, on Unity's main thread. This is the
// valid player-loop context where AddComponent/get_gameObject/Request work.
function installPump() {
    try {
        const BA = Il2Cpp.domain.assembly("BlueArchive").image;
        const upd = BA.class("GameMain").method("Update");
        if (upd.virtualAddress.isNull()) throw new Error("GameMain.Update has no address");
        Interceptor.attach(upd.virtualAddress, {
            onEnter() { if (pending.length || pendingDestroy.length) { drain(); drainDestroy(); } }
        });
        send({ type: "log_line", text: "[pump] GameMain.Update pump installed" });
    } catch (e) {
        send({ type: "log_line", text: "[pump] GameMain.Update unavailable (" + e + ") — using capture-hook drain only" });
    }
}

Il2Cpp.perform(() => {
    // Global agent: capture hook drains queued refresh tasks in the network context each call.
    const n = installCaptureHook(seen, () => {
        if (pending.length || pendingDestroy.length) { drain(); drainDestroy(); }
    });
    installPump();
    send({ type: "armed", count: n });
});

rpc.exports = {
    refresh(taskNames: string[]) {
        seen.clear();
        firstError = true;
        for (const t of taskNames) pending.push({ task: t, attempts: 0 });
        return true;
    },
    listTasks() {
        return Il2Cpp.perform(() => {
            const BA = Il2Cpp.domain.assembly("BlueArchive").image;
            const out: string[] = [];
            for (const k of BA.classes) if (/NetworkTask$/.test(k.name)) out.push(k.name);
            return out.sort();
        }) as unknown as string[];
    },
    // Max AP (energy) cap for an account level, from AccountLevelExcel.APAutoChargeMax
    // via AccountExpInfoData.TryGetValue(level, out excel). Returns 0 if unavailable.
    apCap(level: number) {
        return Il2Cpp.perform(() => {
            try {
                const BA = Il2Cpp.domain.assembly("BlueArchive").image;
                const insts = Il2Cpp.gc.choose(BA.class("MX.Data.AccountExpInfoData"));
                if (insts.length === 0) return 0;
                const box = BA.class("MX.Data.Excel.AccountLevelExcel").alloc();
                const ref = Il2Cpp.reference(box);
                const ok = insts[0].method("TryGetValue", 2).invoke(level, ref);
                if (!ok) return 0;
                return Number((ref.value as any).method("get_APAutoChargeMax").invoke());
            } catch (e) { return 0; }
        }) as unknown as number;
    },
    // Resolve character UniqueId -> localized (in-client language) display name via
    // the game's own LocalizeEtcData.GetName(ParcelType.Character, id). Pure data
    // lookup, so it's safe off the player loop.
    resolveNames(ids: number[]) { return resolveTypedNames(1, ids); },          // Character
    resolveTyped(parcelType: number, ids: number[]) { return resolveTypedNames(parcelType, ids); },
    // Per-student skill materials to max all 4 skills, via the client's own
    // calculator CharacterDBService.GetSkillLevelUpMaterialParcelInfos (static),
    // summed per level over live CharacterDB objects. Returns {uid: {"type:id": amount}}.
    skillMaterialsToMax() {
        return Il2Cpp.perform(() => {
            const out: Record<string, Record<string, number>> = {};
            try {
                const BA = Il2Cpp.domain.assembly("BlueArchive").image;
                const method = BA.class("MX.GameLogic.Service.CharacterDBService").method("GetSkillLevelUpMaterialParcelInfos", 3);
                const SS = BA.class("MX.Logic.BattleEntities.SkillSlot");
                const axes = [
                    { slot: SS.field("ExSkill01").value, cur: "get_ExSkillLevel", cap: 5 },
                    { slot: SS.field("PublicSkill01").value, cur: "get_PublicSkillLevel", cap: 10 },
                    { slot: SS.field("Passive01").value, cur: "get_PassiveSkillLevel", cap: 10 },
                    { slot: SS.field("ExtraPassive01").value, cur: "get_ExtraPassiveSkillLevel", cap: 10 },
                ];
                for (const db of Il2Cpp.gc.choose(BA.class("MX.GameLogic.DBModel.CharacterDB"))) {
                    let uid: string;
                    try { uid = String(Number(db.method("get_UniqueId").invoke())); } catch (e) { continue; }
                    const mats: Record<string, number> = {};
                    for (const ax of axes) {
                        let cur = 0;
                        try { cur = Number(db.method(ax.cur).invoke()); } catch (e) { continue; }
                        for (let lv = cur + 1; lv <= ax.cap; lv++) {
                            try {
                                const cost: any = method.invoke(db, ax.slot, lv);
                                const pis: any = cost.method("get_ParcelInfos").invoke();
                                const n = Number(pis.method("get_Count").invoke());
                                for (let i = 0; i < n; i++) {
                                    const pi: any = pis.method("get_Item").invoke(i);
                                    const key: any = pi.method("get_Key").invoke();
                                    const k = String(key.method("get_Type").invoke()) + ":" + Number(key.method("get_Id").invoke());
                                    mats[k] = (mats[k] || 0) + Number(pi.method("get_Amount").invoke());
                                }
                            } catch (e) { }
                        }
                    }
                    out[uid] = mats;
                }
            } catch (e) { }
            return out;
        }) as unknown as Record<string, Record<string, number>>;
    },
    // Gear (equipment) tier-up materials to max tier, per student, summed across the
    // student's equipped slots. Walks each equipped EquipmentDB's tier-up recipe chain
    // (via the client's TryGetTierUpRecipe + RecipeService.CraftCost) to the max tier.
    // Returns {uid: {"Type:id": amount}} — same shape as skillMaterialsToMax.
    gearMaterialsToMax() {
        return Il2Cpp.perform(() => {
            const out: Record<string, Record<string, number>> = {};
            try {
                const BA = Il2Cpp.domain.assembly("BlueArchive").image;
                const EqDB = BA.class("MX.GameLogic.DBModel.EquipmentDB");
                const eqSvc = BA.class("MX.GameLogic.Service.EquipmentDBService");
                const tryRecipe = eqSvc.method("TryGetTierUpRecipe", 2);
                const nextId = eqSvc.method("TryGetNextTierEquipmentId", 2);
                const craft = BA.class("MX.GameLogic.Service.RecipeService").method("CraftCost", 1);
                const RecipeInfo = BA.class("MX.Data.RecipeInfo");
                const Int64 = Il2Cpp.domain.assembly("mscorlib").image.class("System.Int64");

                // char ServerId -> UniqueId
                const sid2uid: Record<number, number> = {};
                for (const db of Il2Cpp.gc.choose(BA.class("MX.GameLogic.DBModel.CharacterDB"))) {
                    try {
                        sid2uid[Number(db.method("get_ServerId").invoke())] =
                            Number(db.method("get_UniqueId").invoke());
                    } catch (e) { }
                }

                for (const eq of Il2Cpp.gc.choose(EqDB)) {
                    let bound = 0;
                    try { bound = Number(eq.method("get_BoundCharacterServerId").invoke()); } catch (e) { }
                    const uid = sid2uid[bound];
                    if (!bound || uid === undefined) continue;   // only equipped gear
                    const mats = (out[String(uid)] = out[String(uid)] || {});
                    let cur: any = eq;
                    let curId = Number(eq.method("get_UniqueId").invoke());
                    let tier = Number(eq.method("get_Tier").invoke());
                    for (let step = 0; step < 12; step++) {
                        const rref = Il2Cpp.reference(RecipeInfo.alloc());
                        if (!String(tryRecipe.invoke(cur, rref)).includes("true")) break;  // at max tier
                        const ing = Number((rref.value as any).method("get_RecipeIngredientId").invoke());
                        const cost: any = craft.invoke(ing);
                        const pis: any = cost.method("get_ParcelInfos").invoke();
                        const n = Number(pis.method("get_Count").invoke());
                        for (let i = 0; i < n; i++) {
                            const pi: any = pis.method("get_Item").invoke(i);
                            const k: any = pi.method("get_Key").invoke();
                            const key = String(k.method("get_Type").invoke()) + ":" + Number(k.method("get_Id").invoke());
                            mats[key] = (mats[key] || 0) + Number(pi.method("get_Amount").invoke());
                        }
                        const nref = Il2Cpp.reference(Int64.alloc());
                        if (!String(nextId.invoke(curId, nref)).includes("true")) break;
                        const nid = Number(nref.value);
                        const ne: any = EqDB.alloc();
                        ne.method("set_UniqueId").invoke(nid);
                        try { ne.method("set_Tier").invoke(tier + 1); } catch (e) { }
                        cur = ne; curId = nid; tier++;
                    }
                }
            } catch (e) { }
            return out;
        }) as unknown as Record<string, Record<string, number>>;
    },
    // Eleph to max star grade (5*) and to max UE weapon grade, per student, via the
    // client's own calculators. Self-contained (star from CharacterDB, weapon star
    // from WeaponDB). Returns {uid: {eleph_star, eleph_ue}}.
    gradeCostsToMax() {
        return Il2Cpp.perform(() => {
            const out: Record<string, { eleph_star: number; eleph_ue: number }> = {};
            try {
                const BA = Il2Cpp.domain.assembly("BlueArchive").image;
                const svc = BA.class("MX.GameLogic.Service.CharacterDBService");
                const calcChar = svc.method("CalcRemainEligmaCountForCharacterMaxGrade", 2);
                const calcWeap = svc.method("CalcRemainEligmaCountForWeaponMaxGrade", 2);
                const wstar: Record<number, number> = {};
                for (const w of Il2Cpp.gc.choose(BA.class("MX.GameLogic.DBModel.WeaponDB"))) {
                    try {
                        wstar[Number(w.method("get_BoundCharacterServerId").invoke())] =
                            Number(w.method("get_StarGrade").invoke());
                    } catch (e) { }
                }
                for (const db of Il2Cpp.gc.choose(BA.class("MX.GameLogic.DBModel.CharacterDB"))) {
                    try {
                        const uid = Number(db.method("get_UniqueId").invoke());
                        const sid = Number(db.method("get_ServerId").invoke());
                        const star = Number(db.method("get_StarGrade").invoke());
                        const es = Number(calcChar.invoke(star, uid));
                        let eu = 0;
                        if (wstar[sid] !== undefined) {
                            try { eu = Number(calcWeap.invoke(uid, wstar[sid])); } catch (e) { }
                        }
                        out[String(uid)] = { eleph_star: es, eleph_ue: eu };
                    } catch (e) { }
                }
            } catch (e) { }
            return out;
        }) as unknown as Record<string, { eleph_star: number; eleph_ue: number }>;
    },
    // Character cumulative-EXP curve: {level: TotalExp} via CharacterData.GetLevelExpData.
    charExpCurve(maxLevel: number) {
        return Il2Cpp.perform(() => {
            const out: Record<string, number> = {};
            try {
                const BA = Il2Cpp.domain.assembly("BlueArchive").image;
                const insts = Il2Cpp.gc.choose(BA.class("MX.Data.CharacterData"));
                if (insts.length === 0) return out;
                const cd = insts[0];
                for (let lv = 1; lv <= maxLevel; lv++) {
                    try {
                        const ex: any = cd.method("GetLevelExpData", 1).invoke(lv);
                        if (ex) out[lv] = Number(ex.method("get_TotalExp").invoke());
                    } catch (e) { }
                }
            } catch (e) { }
            return out;
        }) as unknown as Record<string, number>;
    }
};

