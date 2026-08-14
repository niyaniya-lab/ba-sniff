import "frida-il2cpp-bridge";

// Spike step 3: capture PLAINTEXT packet JSON by hooking the client's own JSON
// deserializer. The MX response is already decrypted by the time Newtonsoft
// deserializes it, so this sidesteps the RSA/AES key problem entirely.
//
// Read-only: we Interceptor.attach (never replace), so game behavior is untouched.

const ACCOUNT_RX = /AccountDB|CharacterDB|CurrencyDB|Nickname|ServerId|MissionProgress|ScenarioHistory|Parcel|"Level"|AccountLevel|Pyroxene/i;

Il2Cpp.perform(() => {
    console.log("[hook] il2cpp ready. unity", Il2Cpp.unityVersion);

    let image: Il2Cpp.Image;
    try {
        image = Il2Cpp.domain.assembly("Newtonsoft.Json").image;
    } catch (e) {
        console.log("[hook] Newtonsoft.Json assembly not found:", e);
        send({ type: "done" });
        return;
    }

    const targets: Il2Cpp.Method[] = [];
    for (const cname of ["Newtonsoft.Json.JsonConvert", "Newtonsoft.Json.JsonSerializer"]) {
        let klass: Il2Cpp.Class;
        try { klass = image.class(cname); } catch { continue; }
        for (const m of klass.methods) {
            if (!/Deserialize/i.test(m.name)) continue;
            if (m.virtualAddress.isNull()) continue;            // generic/uncompiled
            if (m.parameterCount < 1) continue;
            if (m.parameters[0].type.name !== "System.String") continue;  // want the raw-string entry
            targets.push(m);
        }
    }

    if (targets.length === 0) {
        console.log("[hook] no string-taking Deserialize methods found");
        send({ type: "done" });
        return;
    }

    let hits = 0;
    for (const m of targets) {
        console.log("[hook] attaching:", m.class.name + "." + m.name + "(" + m.parameterCount + ") @" + m.virtualAddress);
        Interceptor.attach(m.virtualAddress, {
            onEnter(args) {
                try {
                    // static il2cpp method: args[0] = first managed param (the String*)
                    const p = args[0];
                    if (p.isNull()) return;
                    const s = new Il2Cpp.String(p).content ?? "";
                    if (s.length < 120) return;
                    if (!ACCOUNT_RX.test(s)) return;
                    hits++;
                    send({ type: "hit", n: hits, len: s.length, method: m.name, head: s.slice(0, 4000) });
                } catch (e) { /* not a string / freed */ }
            }
        });
    }

    console.log("[hook] armed on", targets.length, "method(s). Now navigate the game (lobby, student list, campaign).");
    send({ type: "armed", count: targets.length });
});
