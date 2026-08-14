import "frida-il2cpp-bridge";

// SPIKE: is MX.Data.LocalizeEtcData reachable WITHOUT gc.choose?
//
// lib/mx.ts:resolveTypedNames finds it with `Il2Cpp.gc.choose(...)` — a stop-the-world heap
// walk that hangs the JP client (FINDINGS §11b), which is the only reason JP has no name
// resolution. Nothing about names needs decryption: the client has already loaded and
// decrypted ExcelDB.db into these wrappers. So if the singleton can be reached through a
// static field instead of the heap, JP name resolution works with no gc.choose at all.
//
// This spike only READS and calls a pure lookup. Safe on Global (no anti-cheat).
// It deliberately never calls gc.choose, so a hit here is directly portable to JP.

const TARGET = "MX.Data.LocalizeEtcData";
const PROOF_ID = 10000;              // GetName(Character=1, 10000) must give "Aru"
const PROOF_EXPECT = "Aru";

// Try GetName on a candidate instance. Returns the resolved name, or null if unusable.
function proves(inst: Il2Cpp.Object): string | null {
    try {
        if (inst.isNull()) return null;
        const n = inst.method<Il2Cpp.String>("GetName", 2).invoke(1, PROOF_ID);
        const s = n.isNull() ? null : n.content;
        return s || null;
    } catch (e: any) {
        send({ type: "proof_error", error: e && e.message ? e.message : String(e) });
        return null;
    }
}

Il2Cpp.perform(() => {
    const BA = Il2Cpp.domain.assembly("BlueArchive").image;
    send({ type: "start", image: BA.name, target: TARGET });

    let klass: Il2Cpp.Class;
    try {
        klass = BA.class(TARGET);
    } catch (e: any) {
        send({ type: "fatal", error: `class ${TARGET} not found: ${e}` });
        return;
    }

    // ---- 1. the class's own statics, and its base chain (MX may use a generic base) ----
    const chain: string[] = [];
    for (let k: Il2Cpp.Class | null = klass; k != null; k = k.parent) {
        chain.push(k.type.name);
        for (const f of k.fields) {
            if (!f.isStatic) continue;
            send({ type: "static_field", on: k.type.name, field: f.name, ftype: f.type.name });
            if (f.type.name.indexOf("LocalizeEtcData") < 0) continue;
            try {
                const got = proves(f.value as Il2Cpp.Object);
                send({ type: "CANDIDATE", how: `${k.type.name}.${f.name}`, kind: "static field",
                       resolved: got, works: got === PROOF_EXPECT });
            } catch (e: any) {
                send({ type: "candidate_read_failed", how: `${k.type.name}.${f.name}`,
                       error: e && e.message ? e.message : String(e) });
            }
        }
        // parameterless statics returning the type (classic get_Instance())
        for (const m of k.methods) {
            if (!m.isStatic || m.parameterCount !== 0) continue;
            if (m.returnType.name.indexOf("LocalizeEtcData") < 0) continue;
            try {
                const got = proves(m.invoke() as unknown as Il2Cpp.Object);
                send({ type: "CANDIDATE", how: `${k.type.name}.${m.name}()`, kind: "static method",
                       resolved: got, works: got === PROOF_EXPECT });
            } catch (e: any) {
                send({ type: "candidate_call_failed", how: `${k.type.name}.${m.name}()`,
                       error: e && e.message ? e.message : String(e) });
            }
        }
    }
    send({ type: "base_chain", chain });

    // ---- 2. anything else in the image holding one in a static (a manager/holder) ----
    let scanned = 0, holders = 0;
    for (const k of BA.classes) {
        scanned++;
        let fields: Il2Cpp.Field[];
        try { fields = k.fields; } catch (e) { continue; }
        for (const f of fields) {
            if (!f.isStatic) continue;
            if (f.type.name.indexOf("LocalizeEtcData") < 0) continue;
            holders++;
            try {
                const got = proves(f.value as Il2Cpp.Object);
                send({ type: "CANDIDATE", how: `${k.type.name}.${f.name}`, kind: "holder static",
                       resolved: got, works: got === PROOF_EXPECT });
            } catch (e: any) {
                send({ type: "candidate_read_failed", how: `${k.type.name}.${f.name}`,
                       error: e && e.message ? e.message : String(e) });
            }
        }
    }
    send({ type: "scan_done", scanned, holders });
    send({ type: "done" });
});
