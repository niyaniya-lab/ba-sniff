# How to find classes and methods to call (IL2CPP discovery)

"How did you know `FriendListNetworkTask` existed and could be called?" Two
honest answers: (1) in *this* repo I reused names the earlier Frida work had
already discovered, and (2) that work found them by **dumping the game's IL2CPP
metadata** and reading it. This doc covers both so you can do it yourself for
anything new.

---

## 1. Where the names in this project actually came from

I did not rediscover them from scratch. They were already in the repo's Frida
code, which is the record of the original discovery:

| Name / recipe | Already documented in |
|---|---|
| `Newtonsoft.Json.JsonConvert.DeserializeObject(string)` — the capture point | `frida/agent/capture.ts` |
| `Assets._MX.Program.Scripts.Network.NetworkTaskManager` + `.Request(task, null)` | `frida/agent/capture_refresh.ts` (`MGR_CLASS`, `fireNow`) |
| `FriendListNetworkTask` / `FriendGetListNetworkTask` (the id-card task names) | `shittim_capture.py::try_fetch_id_card` |
| "NetworkTasks are MonoBehaviours → `AddComponent`, not `new()`, then `Request`" | `frida/agent/invoke3.ts` (the spike that figured out the calling recipe) |

So step one for any BA task is always: **grep the repo first.** The Frida agent
is a map of what's already known to work.

---

## 2. The general method: dump the IL2CPP metadata

A Unity IL2CPP game carries its entire type system in two files. Dumping them
gives you a browsable listing of **every** class, method, field, namespace, and
each method's RVA (address). This is the authoritative map.

**Inputs (in the game folder, e.g. `…/BlueArchive/`):**
- `GameAssembly.dll` — the compiled code.
- `BlueArchive_Data/il2cpp_data/Metadata/global-metadata.dat` — the type metadata.

**Tools (pick one):**
- **Il2CppDumper** — https://github.com/Perfare/Il2CppDumper — point it at the two
  files, get `dump.cs`: a C#-like listing of everything.
- **Il2CppInspector** — https://github.com/djkaty/Il2CppInspector — same idea,
  richer output (can emit headers, Python/JSON, ready-made structs).

> Metadata can be encrypted/obfuscated on some titles. For BA Global these tools
> work as-is; see `jp-name-sources-investigated` in memory — JP's `ExcelDB.db`
> is encrypted, but the IL2CPP metadata here is not.

**Then just search `dump.cs`.** Everything below is a text search:
- Want the id-card fetch? `grep -i friendidcard dump.cs`, `grep NetworkTask dump.cs`.
- Found `FriendListNetworkTask`? The line above it shows its **namespace**; the
  `// RVA: 0x…` comments show which methods are compiled (have an address).
- Need the thing that runs a task? `grep NetworkTaskManager dump.cs` → read its
  methods → `Request`.
- Need the capture point? `grep -n "class JsonConvert" dump.cs` → its
  `DeserializeObject` overloads and their signatures.

Reading a signature tells you how to call it:

```csharp
// namespace tells you class_from_name(image, "<namespace>", "NetworkTaskManager")
public class NetworkTaskManager : MonoBehaviour {
    // instance method (no 'static') -> needs a live 'this' (see §4)
    // 2 params -> class_get_method_from_name(cls, "Request", 2)
    public void Request(NetworkTask task, Action<TaskState> onFinished) { }
}
```

- **`static` or not** → do you need a `this` object (§4) or can you pass NULL.
- **parameter count** → the `argc` for `il2cpp_class_get_method_from_name`.
- **parameter types** → what to put in the `il2cpp_runtime_invoke` params array
  (reference types: pass the object pointer directly; value types: pointer to
  the value).
- **`// RVA: 0x…`** present → the method is compiled and hookable/callable; a
  missing/zero RVA means a generic/uncompiled definition (skip it — this is the
  same `methodPointer == null` filter `observe.cpp` and `recon.cpp` use).

---

## 3. Live discovery without a dumper (the runtime API)

You can also enumerate the type system at runtime with the same `il2cpp_*` API
`il2cpp_min.h` already wraps — no external tool. This is what `recon.cpp` does
for `JsonConvert`, and what the Frida agent's `listTasks()` did for tasks:

- `il2cpp_domain_get_assemblies` → each assembly's image (`il2cpp_find_image`).
- `il2cpp_image_get_class_count` + `il2cpp_image_get_class` → every class in an
  image; `il2cpp_class_get_name` / `il2cpp_class_get_namespace` to filter (e.g.
  name ends with `NetworkTask`).
- `il2cpp_class_get_methods` + `il2cpp_method_get_name` / `_param_count` /
  `_get_param` + `il2cpp_type_get_name` → every method and its signature.
- `il2cpp_method_ptr` (first field of `MethodInfo`) → the native address; non-null
  means compiled.

### The native lister (`list.cpp` / `list.dll`)

`list.cpp` is that probe. Inject it and it walks every loaded class and writes
matching classes + method signatures (with live RVAs) to `list.log`.

```bat
REM 1. With the game running, put a substring to search in native\list_query.txt:
echo NetworkTask> native\list_query.txt      REM or JsonConvert, NetworkTaskManager, GameObject...
REM    (empty file = dump EVERY class -- large)
REM 2. Build once, then inject:
native\build.bat list.cpp list.dll
.venv\Scripts\python.exe native\inject.py native\list.dll
REM 3. Read native\list.log
```

Output looks like:

```
Assets._MX...Friend.FriendListNetworkTask
    [rva=0x09..] System.Void .ctor()
    [rva=0x09..] System.Void OnResponse(...)
Assets._MX.Program.Scripts.Network.NetworkTaskManager
    [rva=0x09..] System.Void Request(NetworkTask, System.Action`1<TaskState>)
```

`rva=0x0` means a generic/uncompiled method (skip it). It's a new DLL, so you can
inject it into the *same* running client that already has `observe.dll` loaded.

### Offline dump with Il2CppDumper (the standard way)

For a full, browsable listing without running anything in-process:

1. Download **Il2CppDumper** (https://github.com/Perfare/Il2CppDumper).
2. Run it against the two files (paths for the Steam install used here):
   - `D:\SteamLibrary\steamapps\common\BlueArchive\GameAssembly.dll`
   - `D:\SteamLibrary\steamapps\common\BlueArchive\BlueArchive_Data\il2cpp_data\Metadata\global-metadata.dat`
3. It writes `dump.cs` (every class/method/field + RVAs) plus `script.json` and
   header/struct files. Then `grep` `dump.cs` as in §2.

The dump is faster for broad exploration and includes fields/offsets; the native
lister is better when you want *current* runtime addresses or don't want to
install a tool.

---

## 4. Two gotchas that bit us (and will bite again)

1. **MonoBehaviours can't be `new()`-ed.** A `NetworkTask` is a Unity component,
   so you create it with `GameObject.AddComponent(type)`, not `il2cpp_object_new`.
   `object_new` is only for plain managed objects (like the throwaway
   `GameObject` itself). This is why `observe.cpp::try_fire_friendlist` builds a
   GameObject first, then AddComponents the task. (Discovered in `invoke3.ts`.)

2. **Finding a *live instance* needs a heap walk, and match by subclass.** A
   class from `class_from_name` is just the type; to *call an instance method*
   you need an actual live object. There's no static path to most BA singletons
   (see `docs/FINDINGS.md` and memory `native-il2cpp-recon`), so we scan the GC
   heap (`il2cpp_gc_foreach_heap`) for one — and match with
   `il2cpp_class_is_subclass_of`, because the live object is often a *subclass*
   of the type you looked up (exact pointer match found nothing across 218k
   objects). Keep the walk callback to cheap pointer ops only — calling
   `il2cpp_class_get_name` per object there crashed the walk.

---

## 5. Quick recipe for "I want to fire task X"

1. `grep -i X dump.cs` → get the exact class name + namespace + confirm it ends
   in `NetworkTask` and has a compiled RVA.
2. Confirm `NetworkTaskManager` + its `Request(task, onFinished)` signature.
3. In native: `class_from_name`/`il2cpp_find_class` the task + manager; heap-walk
   (`il2cpp_gc_choose_one`) for a live manager; build a GameObject, `AddComponent`
   the task type (`type_get_object(class_get_type(taskClass))`), `Request(task, null)`
   — all via `il2cpp_runtime_invoke`. `observe.cpp::try_fire_friendlist` is the
   working template; copy it and swap the task name.
4. Run it from a context on the game's main/network thread (our `DeserializeObject`
   detour is one) — Unity calls like `AddComponent` only work there.
