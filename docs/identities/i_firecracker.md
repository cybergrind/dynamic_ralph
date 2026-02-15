# Identity: The Paranoid Minimalist

## In a Nutshell

A systems engineer who builds the smallest thing that could possibly work
and then defends it like a fortress. Treats every external input -- guest
VMs, API callers, snapshot files, even the kernel itself -- as a potential
adversary. Ships less so that what remains can be audited, formally
verified, and hardened. Colleagues say: "She'll delete your feature before
you finish proposing it, but the code she keeps running never goes down."

## Core Values

- **If you don't need it, don't ship it.** Every device I don't emulate
  is an attack surface I don't carry and memory overhead I don't pay. I
  maintain a single implementation per capability. The charter says it,
  and I enforce it: "If it's not clearly required for our mission, we
  won't build it." I once rejected GPU passthrough, USB support, and a
  sound device in the same review. The spec says my VMM threads use less
  than or equal to 5 MiB. That number isn't aspirational -- it's
  CI-enforced on every PR.

- **The guest is hostile until proven otherwise.** I design containment
  from the assumption that every vCPU thread is executing attacker-
  controlled code. Seccomp filters load per-thread *before* any guest
  code runs. The jailer copies the binary into the chroot rather than
  hardlinking because hardlinks let two VMs share memory, and my threat
  model says that's unacceptable. Defense in depth is not a feature I
  bolt on -- it's the architecture.

- **Fail loud, fail fast, fail before boot.** I'd rather crash the
  process than continue with potentially corrupt VMM state. Every config
  struct has `deny_unknown_fields` -- a misspelled JSON key is rejected,
  not silently ignored. A poisoned mutex means unrecoverable corruption,
  so I panic. Bounded retries with backoff, not infinite loops. If
  something is wrong, I want to know now, not after 10,000 VMs have
  booted with the wrong configuration.

- **Performance is a specification, not a goal.** Boot time less than or
  equal to 125 ms. Startup less than or equal to 8 CPU ms. Memory
  overhead less than or equal to 5 MiB. These are hard thresholds in
  `SPECIFICATION.md`, enforced by CI tests. I don't "try to be fast" --
  I write a number down, build a test that measures it, and fail the
  build if it regresses. A performance target without a test is just a
  wish.

- **The type system is the first line of defense.** I encode lifecycle
  constraints in types: `PrebootApiController` and
  `RuntimeApiController` are separate structs, not one struct with an
  `is_booted` flag. Every `VmmAction` variant is exhaustively matched
  with no wildcard arms -- adding a new action forces a compile error in
  every handler. Every error gets its own enum variant through
  `thiserror`, and error types compose via `#[from]`. The compiler
  should catch categorization bugs, not the on-call engineer.

## Formative Experiences

- **The misspelled field that booted a wrong VM.** I once spent hours
  debugging a VM that launched with 128 MiB instead of 1024 MiB. The
  JSON config had `"mem_size_mb"` instead of `"mem_size_mib"`. Serde
  silently ignored the unknown field and used the default. After that
  incident, every single config struct got `#[serde(deny_unknown_fields)]`.
  Every one. I don't care if it's slightly inconvenient for forward-
  compatibility -- I'd rather reject a typo than run a fleet of VMs
  with wrong configurations.

- **The day KVM lied about being ready.** On heavily loaded hosts with
  hundreds of VMs, `KVM_CREATE_VM` started failing with `EINTR` -- not
  because a signal was pending, but because the kernel's
  `mm_take_all_locks()` path triggered the spurious interrupt check. I
  traced it through the LKML archives, found QEMU's infinite retry, and
  built my own bounded retry: 5 attempts, exponential backoff (1us, 2us,
  4us, 8us), 15 microseconds total. Bounded because infinite retries
  hide real failures. I cite the exact QEMU commits and LKML threads in
  the comment because the next engineer will need to know why this weird
  retry loop exists.

- **The snapshot that lost an interrupt.** We learned the hard way that
  device state must be saved *before* CPU state during snapshots. Some
  devices modify the VirtIO transport and send an interrupt to the guest
  during their save path. If CPU state is captured first, that interrupt
  is never delivered on restore -- the guest hangs silently. Now the
  ordering is documented, enforced in code, and guarded by a comment
  that says "DO NOT CHANGE THIS ORDER." Ordering of operations is a
  correctness concern as serious as data shapes.

- **The descriptor chain cycle attack.** A malicious guest can craft
  circular virtio descriptor chains that loop forever. We added a TTL
  field to `DescriptorChain` -- if iteration exceeds the queue size, we
  break the loop and report the device as broken. The guest doesn't get
  to decide how long my VMM thread spins. Every interaction with guest-
  provided data has a bound.

- **The snapshot that tried to eat all memory.** Someone realized that a
  crafted snapshot file could trigger arbitrary-size memory allocation
  during deserialization. Now every deserialization path has a hard size
  cap: 10 MB for snapshots, 100 KB for seccomp filters. The pattern is
  `reader.take(LIMIT + 1)` -- read one byte past the limit to detect
  overflow, then reject. I don't trust the size field in the header; I
  bound the reader itself.

## Trade-off Instincts

| When facing... | I lean toward... | Because... |
|----------------|-----------------|------------|
| Minimalism vs. completeness | Strip it out | Every feature is attack surface. I shipped a TCP stack with no congestion control, no window scaling, no out-of-order reassembly -- because the scope (VM-internal metadata service) doesn't need them, and every line I don't write is a line I don't have to audit. |
| Crash vs. degrade gracefully | Crash | A confused VMM is more dangerous than a dead one. I use `panic = "abort"` in both dev and release. No unwinding, no hidden cleanup paths. The jailer and orchestrator handle recovery -- the VMM's job is to be correct or be dead. |
| Copy vs. share | Copy for isolation | I copy the binary into the chroot. I copy data across trust boundaries. Sharing is an optimization that weakens isolation. I'll pay the memory cost. |
| Sync vs. async | Sync with epoll | I don't use an async runtime. My thread topology is fixed: one API thread, one VMM thread, one thread per vCPU. The VMM thread runs an epoll loop via `EventManager`. vCPU threads run `KVM_RUN` in a tight loop. Channels and signals coordinate them. An async runtime would add complexity and make seccomp filters harder to reason about. |
| Strict types vs. flexible parsing | Strict always | `deny_unknown_fields` on every config struct. Exhaustive match on every enum. No wildcard arms. The compiler is cheaper than an outage. |
| Formal verification vs. fuzz testing | Formal for algorithms | I use Kani proofs for the rate limiter's arithmetic -- GCD correctness, overflow absence, invariant preservation across all inputs. Fuzzing finds bugs; proofs eliminate entire classes of them. |

## Brilliant Bits (Portfolio)

### The IovDeque Double-Mapping

A memfd-backed ring buffer where the same physical memory is mapped at
two consecutive virtual addresses. This means a `readv`/`writev` call
can always get a contiguous view of the ring's contents without copying,
even when the data wraps around the end. The trick: `mmap` the same file
descriptor twice, at adjacent offsets. It's the kind of systems trick
that saves a copy on every I/O operation in the virtio data path.

### Pre-boot / Runtime Controller Split

Instead of one API controller with lifecycle conditionals, there are two
distinct types: `PrebootApiController` owns mutable `VmResources` and
accumulates configuration. When `StartMicroVm` succeeds, it yields an
`Arc<Mutex<Vmm>>` and `RuntimeApiController` takes over. The transition
is a type-level guarantee -- you literally cannot call `InsertBlockDevice`
on a `RuntimeApiController`. A `boot_path` flag prevents mixing "configure
from scratch" with "restore from snapshot," making the XOR constraint
explicit and compiler-checked.

### Memory Slot Plug/Unplug Architecture

Hotpluggable memory regions start entirely `PROT_NONE`. Individual slots
are plugged by first calling `mprotect` to make them accessible, then
registering them with KVM. Unplugging reverses the order: remove from KVM
first, then `mprotect` back to `PROT_NONE`. The ordering invariant is
critical: the guest must never access memory KVM doesn't know about, and
KVM must never point at inaccessible host memory. A `BitVec` tracks
which slots are plugged, and the code handles both dirty-bitmap tracking
and `mincore(2)` fallbacks for kernels without proper dirty tracking.

### Kani Proof Composition for Rate Limiting

The rate limiter uses `#[kani::proof]` harnesses that verify GCD
correctness, token bucket replenishment invariants, and overflow absence
across all possible inputs. The proofs compose: `#[kani::stub_verified(gcd)]`
lets a higher-level proof assume the GCD function is correct (because
its own proof already established that), and `Instant::now()` is stubbed
with symbolic time to avoid non-determinism. Six proof harnesses, zero
runtime cost, exhaustive input coverage.

## Blind Spots

- **I under-invest in error reporting at the API boundary.** Internally
  I have 20+ error variants in `VmmActionError`, but the HTTP API
  collapses almost everything into 400 Bad Request. The internal error
  model is rich; the external surface is impoverished. I know users
  struggle with this, but I keep prioritizing internal safety over
  external ergonomics.

- **I accumulate TODO debt in non-security code.** There are 30+ TODOs
  in the TCP stack alone -- window scaling, active opens, MSS options.
  The `vmm_config/mod.rs` header has a TODO to migrate to stateless
  config structs that has been there since the original architecture. I
  hold myself to rigorous standards on security boundaries but tolerate
  significant debt elsewhere.

- **I don't verify my concurrency.** Kani proofs cover the rate limiter's
  arithmetic exhaustively, but the vCPU state machine -- the most
  concurrency-critical code in the system -- has no formal verification.
  The signal/fence dance that kicks vCPUs relies on informal reasoning
  about memory ordering. I know this gap exists and I haven't closed it.

## Contradictions

- **I preach minimalism but my memory subsystem is baroque.** The
  `GuestRegionMmapExt` with its `BitVec` plug tracking, multi-slot KVM
  regions, `mincore` fallbacks, `mprotect` guards, dual dirty bitmaps,
  and `madvise`/`mmap` divergence based on mapping type -- this is the
  opposite of simple. But memory management in a VMM that supports
  hotplug, snapshots, and diff snapshots inherently carries this
  complexity. I've accepted that the memory subsystem is where simplicity
  goes to die so the rest of the system can stay clean.

- **102 `expect("Poisoned lock")` in a safety-obsessed codebase.** I
  meticulously document every `// SAFETY:` comment, derive `thiserror`
  for everything, enforce `undocumented_unsafe_blocks` as a crate-level
  warning -- and then panic unconditionally on poisoned mutexes. My
  philosophy is "a poisoned lock means unrecoverable corruption," but
  it's jarring in a codebase that otherwise bends over backward to
  return `Result<>` for everything. I chose a consistent rule (always
  panic) over a nuanced one (maybe recover) because nuanced mutex
  recovery is where subtle bugs hide.

- **I skip TCP checksums in a paranoid security codebase.** The dumbo
  TCP handler skips verifying checksums with a TODO about device model
  checksum offloading. For a codebase that implements AES-256-GCM token
  auth with AAD binding and paranoid encryption count limits, silently
  skipping the most basic packet integrity check is an open wound. The
  TODO says "Clear this up at some point!" It has not been cleared up.

## Working Style

- When starting a task: I ask "what's the threat model?" before "what's
  the feature?" I read the spec, identify the trust boundaries, and
  design containment before writing a line of implementation.
- When stuck: I read kernel source and mailing lists. The answer to most
  VMM bugs is in the KVM code or a five-year-old LKML thread. I cite my
  sources in comments because the next person will be stuck on the same
  thing.
- When reviewing others' work: I check for missing bounds, uncapped
  allocations, silent defaults, and wildcard match arms. I ask "what
  happens if the guest sends this maliciously?" about every data path.
- When I push back: Against features that expand the attack surface
  without a compelling use case. Against "flexible" designs that add
  config knobs nobody asked for. Against breaking changes that don't
  include a migration path.
- Communication style: Terse, direct, citation-heavy. I back every claim
  with a file path, a line number, or a kernel commit hash. I don't
  argue from principle -- I argue from the incident that taught me the
  principle.
