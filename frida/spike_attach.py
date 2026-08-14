"""
Spike step 1: can Frida attach to BlueArchive.exe, and is it IL2CPP?

Run with the game ALREADY RUNNING (at title or lobby):
    ..\.venv\Scripts\python.exe spike_attach.py

Answers three gating questions:
  1. Does Frida attach at all (or is there anti-tamper blocking it)?
  2. Is GameAssembly.dll present (=> Unity IL2CPP => frida-il2cpp-bridge works)?
  3. Are known anti-cheat modules loaded (GameGuard etc.) that we must plan around?
"""
import sys
import frida

PROC = "BlueArchive.exe"

JS = r"""
const wanted = ["gameassembly", "unityplayer", "mono", "il2cpp",
                "gameguard", "gamemon", "npgg", "aegis", "xigncode",
                "nprotect", "anticheat", "easyanticheat"];
const mods = Process.enumerateModules();
const hits = [];
for (const m of mods) {
    const n = m.name.toLowerCase();
    if (wanted.some(w => n.includes(w))) {
        hits.push({name: m.name, base: m.base.toString(), size: m.size, path: m.path});
    }
}
send({type: "summary", total: mods.length, arch: Process.arch, hits: hits});
// also report whether the il2cpp export exists (strong IL2CPP signal)
const ga = Process.findModuleByName("GameAssembly.dll");
if (ga) {
    const init = Module.findExportByName("GameAssembly.dll", "il2cpp_init");
    const domain = Module.findExportByName("GameAssembly.dll", "il2cpp_domain_get");
    send({type: "il2cpp", il2cpp_init: init ? init.toString() : null,
          il2cpp_domain_get: domain ? domain.toString() : null});
}
"""

def on_message(msg, data):
    if msg.get("type") == "error":
        print("[JS ERROR]", msg.get("description"))
        return
    p = msg.get("payload", {})
    if p.get("type") == "summary":
        print(f"[+] ATTACHED. arch={p['arch']} modules={p['total']}")
        print("[+] relevant modules:")
        for h in p["hits"]:
            print(f"    {h['name']:24} base={h['base']} size={h['size']}")
        if not any("gameassembly" in h["name"].lower() for h in p["hits"]):
            print("    (!) GameAssembly.dll NOT found - not IL2CPP or renamed")
        cheats = [h["name"] for h in p["hits"] if any(
            c in h["name"].lower() for c in
            ["gameguard","gamemon","npgg","aegis","xigncode","nprotect","anticheat","easyanticheat"])]
        print(f"[+] anti-cheat modules detected: {cheats if cheats else 'NONE (good)'}")
    elif p.get("type") == "il2cpp":
        print(f"[+] IL2CPP exports: il2cpp_init={p['il2cpp_init']} il2cpp_domain_get={p['il2cpp_domain_get']}")
        print("    -> frida-il2cpp-bridge should be able to resolve classes by name.")

def main():
    try:
        session = frida.attach(PROC)
    except frida.ProcessNotFoundError:
        print(f"[-] '{PROC}' not running. Launch Blue Archive first, then re-run.")
        sys.exit(1)
    except frida.PermissionDeniedError as e:
        print(f"[-] Permission denied attaching (try elevated PowerShell): {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[-] Attach failed ({type(e).__name__}): {e}")
        print("    If this hangs or crashes the game, anti-tamper is likely blocking Frida.")
        sys.exit(1)

    print(f"[+] frida.attach('{PROC}') OK")
    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()
    print("[+] probe complete. Detaching.")
    session.detach()

if __name__ == "__main__":
    main()
