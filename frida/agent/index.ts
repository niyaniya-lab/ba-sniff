import "frida-il2cpp-bridge";

// Spike step 2: locate the MX packet crypto / decode classes and list their
// methods, so we can pick the exact method to hook for plaintext.
//
// We DON'T hook yet — this is reconnaissance. It prints candidate classes whose
// names match the crypto/packet/session machinery seen in Shittim-Server's RE
// (PacketCryptManager, HttpGameMessage, HttpGameSession, GatewayController...).

Il2Cpp.perform(() => {
    console.log("[il2cpp] ready. unityVersion =", Il2Cpp.unityVersion);

    // Names that matter for reading decrypted packets, from the Shittim RE notes.
    const rx = /Crypt|Packet|HttpGame|GameSession|Gateway|Hybrid|Aes|Protocol|Serializer|MessagePack|Response|Request/i;
    // Skip obvious noise that also matches (UI, tweening, etc.) to keep output sane.
    const skip = /UI|Tween|Animation|Localization|Addressable|Editor/i;

    let matched = 0;
    for (const assembly of Il2Cpp.domain.assemblies) {
        let image: Il2Cpp.Image;
        try { image = assembly.image; } catch { continue; }
        for (const klass of image.classes) {
            const name = klass.name;
            if (!rx.test(name) || skip.test(name)) continue;
            matched++;
            console.log("\nCLASS " + klass.type.name + "   [" + assembly.name + "]");
            let n = 0;
            for (const m of klass.methods) {
                // show methods that look like (de/en)crypt / decode / deserialize entry points
                if (/crypt|decode|encode|deserial|serial|decompress|read|parse|handle|process/i.test(m.name)) {
                    const params = m.parameters.map(p => p.type.name).join(", ");
                    console.log("   M " + m.returnType.name + " " + m.name + "(" + params + ")  @" + m.virtualAddress);
                    n++;
                }
            }
            if (n === 0) console.log("   (no obvious decode/crypt methods by name)");
        }
    }
    console.log("\n[il2cpp] matched classes:", matched);
    console.log("[il2cpp] recon done.");
});
