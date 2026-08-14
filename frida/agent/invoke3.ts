import "frida-il2cpp-bridge";

// Spike B invoke, take 2: NetworkTasks are MonoBehaviours, so create them via
// GameObject.AddComponent (not new()), then NetworkTaskManager.Request(task, null).
// Bundles the capture hook so we see the Campaign_List (6000) reply come back.

const TARGET_PROTO = "6000";
const TASK_CLASS = "CampaignListNetworkTask";

function installCaptureHook() {
    const image = Il2Cpp.domain.assembly("Newtonsoft.Json").image;
    for (const cname of ["Newtonsoft.Json.JsonConvert", "Newtonsoft.Json.JsonSerializer"]) {
        let klass: Il2Cpp.Class;
        try { klass = image.class(cname); } catch { continue; }
        for (const m of klass.methods) {
            if (!/Deserialize/i.test(m.name) || m.virtualAddress.isNull()) continue;
            if (m.parameterCount < 1 || m.parameters[0].type.name !== "System.String") continue;
            Interceptor.attach(m.virtualAddress, {
                onEnter(args) {
                    try {
                        const p = args[0];
                        if (p.isNull()) return;
                        const s = new Il2Cpp.String(p).content ?? "";
                        if (s.indexOf('"Protocol":' + TARGET_PROTO) !== -1) {
                            console.log(`\n[REPLY proto ${TARGET_PROTO}] len=${s.length}\n${s.slice(0, 320)}`);
                        }
                    } catch (e) { }
                }
            });
        }
    }
    console.log("[invoke3] capture hook installed.");
}

Il2Cpp.perform(() => { installCaptureHook(); });

Il2Cpp.perform(() => {
    try {
        const BA = Il2Cpp.domain.assembly("BlueArchive").image;
        const core = Il2Cpp.domain.assembly("UnityEngine.CoreModule").image;
        const GameObject = core.class("UnityEngine.GameObject");
        const Component = core.class("UnityEngine.Component");

        const mgr = Il2Cpp.gc.choose(BA.class("Assets._MX.Program.Scripts.Network.NetworkTaskManager"))[0];
        console.log("[invoke3] manager:", mgr, "cachedPtr:", mgr.field("m_CachedPtr").value);

        // manager's GameObject (Component.get_gameObject)
        const go = mgr.method<Il2Cpp.Object>("get_gameObject").invoke();
        console.log("[invoke3] gameObject:", go);

        // AddComponent(System.Type) -> properly-backed MonoBehaviour
        const taskClass = BA.class(TASK_CLASS);
        const typeObj = taskClass.type.object;                      // System.Type of the task
        const task = go.method<Il2Cpp.Object>("AddComponent", 1).invoke(typeObj);
        console.log("[invoke3] task via AddComponent, cachedPtr:", task.field("m_CachedPtr").value);

        // fire it (null Action<TaskState>; capture hook catches the reply)
        try {
            mgr.method("Request").invoke(task, NULL);
            console.log("[invoke3] Request() OK — waiting ~10s for reply...");
        } catch (e: any) {
            console.log("[invoke3] Request(null cb) threw:", e && e.message ? e.message : e);
        }
    } catch (e: any) {
        console.log("[invoke3] setup error:", e && e.message ? e.message : e, e && e.stack ? e.stack : "");
    }
    setTimeout(() => send({ type: "done" }), 11000);
}, "main");
