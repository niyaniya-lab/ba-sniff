import "frida-il2cpp-bridge";

// Shared MX helpers used by both the Global (capture_refresh) and JP (jp_agent) agents.

// Install the JSON-deserialize capture hook: forwards every MX packet as plaintext via
// send({type:"packet",...}). `afterEach` runs on every deserialize call — Global uses it
// to drain queued refresh tasks in the network-processing context; JP passes nothing
// (no pump, keeping the load path light so it never starves the anti-cheat'd client).
export function installCaptureHook(seen: Set<string>, afterEach?: () => void): number {
    const image = Il2Cpp.domain.assembly("Newtonsoft.Json").image;
    let n = 0;
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
                        if (!p.isNull()) {
                            const s = new Il2Cpp.String(p).content ?? "";
                            if (s.length >= 20 && (s.indexOf('"Protocol"') !== -1 || s.indexOf('"protocol"') !== -1)) {
                                const key = s.length + ":" + s.slice(0, 48);
                                if (!seen.has(key)) { seen.add(key); send({ type: "packet", len: s.length, json: s }); }
                            }
                        }
                    } catch (e) { }
                    if (afterEach) afterEach();
                }
            });
            n++;
        }
    }
    return n;
}

// Resolve names for a ParcelType via LocalizeEtcData.GetName (Character=1, Equipment=3, Item=4).
// Pure data lookup, but the gc.choose is only cheap when the heap is stable — i.e. in the
// LOBBY, not during loading (a mid-load sweep starves the JP client). Returns {id: name|null}.
export function resolveTypedNames(parcelType: number, ids: number[]) {
    return Il2Cpp.perform(() => {
        const out: Record<string, string | null> = {};
        try {
            const BA = Il2Cpp.domain.assembly("BlueArchive").image;
            const insts = Il2Cpp.gc.choose(BA.class("MX.Data.LocalizeEtcData"));
            if (insts.length === 0) return out;
            const led = insts[0];
            for (const id of ids) {
                try {
                    const r = led.method("GetName", 2).invoke(parcelType, id);
                    out[id] = r ? ((r as any).content ?? null) : null;
                } catch (e) { out[id] = null; }
            }
        } catch (e) { }
        return out;
    }) as unknown as Record<string, string | null>;
}
