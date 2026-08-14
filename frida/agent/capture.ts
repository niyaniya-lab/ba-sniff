import "frida-il2cpp-bridge";

// Production capture: hook the client's JSON deserializer and forward EVERY MX
// packet (any deserialized object with a top-level "Protocol" field) as plaintext.
// The response is already decrypted by the time Newtonsoft sees it, so this needs
// no keys and is resolved by NAME (survives patches / key rotation).
//
// Read-only: Interceptor.attach, never replaces — game behaviour is untouched.

Il2Cpp.perform(() => {
    console.log("[capture] il2cpp ready, unity", Il2Cpp.unityVersion);

    let image: Il2Cpp.Image;
    try {
        image = Il2Cpp.domain.assembly("Newtonsoft.Json").image;
    } catch (e) {
        console.log("[capture] FATAL: Newtonsoft.Json not found (game update may have changed the JSON lib):", e);
        send({ type: "fatal", reason: "no-newtonsoft" });
        return;
    }

    const methods: Il2Cpp.Method[] = [];
    for (const cname of ["Newtonsoft.Json.JsonConvert", "Newtonsoft.Json.JsonSerializer"]) {
        let klass: Il2Cpp.Class;
        try { klass = image.class(cname); } catch { continue; }
        for (const m of klass.methods) {
            if (!/Deserialize/i.test(m.name)) continue;
            if (m.virtualAddress.isNull()) continue;                       // generic/uncompiled
            if (m.parameterCount < 1) continue;
            if (m.parameters[0].type.name !== "System.String") continue;   // raw-string entry
            methods.push(m);
        }
    }

    if (methods.length === 0) {
        console.log("[capture] FATAL: no string-taking Deserialize methods");
        send({ type: "fatal", reason: "no-deserialize-method" });
        return;
    }

    // Light per-session dedup so the same packet isn't forwarded repeatedly.
    const seen = new Set<string>();

    for (const m of methods) {
        Interceptor.attach(m.virtualAddress, {
            onEnter(args) {
                try {
                    const p = args[0];
                    if (p.isNull()) return;
                    const s = new Il2Cpp.String(p).content ?? "";
                    if (s.length < 20) return;
                    if (s.indexOf('"Protocol"') === -1 && s.indexOf('"protocol"') === -1) return;
                    const key = s.length + ":" + s.slice(0, 48);
                    if (seen.has(key)) return;
                    seen.add(key);
                    send({ type: "packet", len: s.length, json: s });
                } catch (e) { /* not a managed string / freed */ }
            }
        });
    }

    console.log("[capture] armed on", methods.length, "deserialize method(s).");
    send({ type: "armed", count: methods.length });
});
