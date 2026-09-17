"""Linux child-process confinement for chat tools; also runs as a standalone launcher.

Landlock is inherited by all CLI tools. Network rules restrict TCP *ports*, not
addresses. Only the two per-turn bridge ports are allowed; UDP and UNIX sockets
are denied by seccomp. The parent bridge, never the CLI, holds provider secrets.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import signal
import sys

FS_READ = (1 << 0) | (1 << 2) | (1 << 3)
FS_ALL = (1 << 15) - 1
NET_CONNECT = 2


def _checked(result: int) -> int:
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return result


def confine(*, read_paths: list[str], write_paths: list[str], ports: list[int]) -> None:
    """Fail closed unless Linux Landlock ABI 4 and libseccomp are available."""
    libc = ctypes.CDLL(None, use_errno=True)
    if _checked(libc.syscall(444, 0, 0, 1)) < 4:
        raise RuntimeError("chat isolation requires Landlock ABI 4")

    class Ruleset(ctypes.Structure):
        _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("access", ctypes.c_uint64), ("fd", ctypes.c_int32)]

    class NetRule(ctypes.Structure):
        _fields_ = [("access", ctypes.c_uint64), ("port", ctypes.c_uint64)]

    attrs = Ruleset(FS_ALL, 3)
    ruleset = _checked(libc.syscall(444, ctypes.byref(attrs), ctypes.sizeof(attrs), 0))
    try:
        for writable, paths in ((False, read_paths), (True, write_paths)):
            for value in paths:
                path = Path(value).resolve(strict=True)
                access = FS_ALL if writable else FS_READ
                if not path.is_dir():
                    access &= (1 << 0) | (1 << 1) | (1 << 2) | (1 << 14)
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                try:
                    rule = PathRule(access, fd)
                    _checked(libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0))
                finally:
                    os.close(fd)
        for port in ports:
            rule = NetRule(NET_CONNECT, port)
            _checked(libc.syscall(445, ruleset, 2, ctypes.byref(rule), 0))
        _checked(libc.prctl(38, 1, 0, 0, 0))  # PR_SET_NO_NEW_PRIVS
        # Load before applying filesystem restrictions so only library paths
        # needed by the actual CLI, rather than Python's loader, are permitted.
        _restrict_syscalls()
        _checked(libc.syscall(446, ruleset, 0))
    finally:
        os.close(ruleset)


def _restrict_syscalls() -> None:
    sec = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    sec.seccomp_init.argtypes = [ctypes.c_uint32]
    sec.seccomp_init.restype = ctypes.c_void_p
    sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sec.seccomp_syscall_resolve_name.restype = ctypes.c_int
    sec.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
    sec.seccomp_load.argtypes = [ctypes.c_void_p]
    sec.seccomp_release.argtypes = [ctypes.c_void_p]

    class Compare(ctypes.Structure):
        _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_uint),
                    ("a", ctypes.c_uint64), ("b", ctypes.c_uint64)]

    ctx = sec.seccomp_init(0x7FFF0000)  # ALLOW
    if not ctx:
        raise RuntimeError("cannot initialize chat seccomp")
    try:
        def deny(name: str, *conditions: Compare) -> None:
            number = sec.seccomp_syscall_resolve_name(name.encode())
            if number < 0:
                return
            values = (Compare * len(conditions))(*conditions)
            result = sec.seccomp_rule_add_array(ctx, 0x00050000 | errno.EPERM,
                                               number, len(values), values)
            if result != 0:
                raise RuntimeError("cannot install chat seccomp rule")

        for name in ("ptrace", "process_vm_readv", "process_vm_writev", "pidfd_getfd",
                     "kill", "tkill", "tgkill", "pidfd_send_signal", "bpf",
                     "mount", "umount2", "pivot_root", "chroot", "setns", "unshare",
                     "open_by_handle_at", "io_uring_setup", "perf_event_open",
                     "chmod", "fchmod", "fchmodat", "fchmodat2",
                     "chown", "fchown", "lchown", "fchownat",
                     "utime", "utimes", "futimesat", "utimensat",
                     "setxattr", "lsetxattr", "fsetxattr",
                     "removexattr", "lremovexattr", "fremovexattr"):

            deny(name)
        # Tokio needs an already-connected UNIX stream pair for signal wakeups.
        # No fresh UNIX socket can be created or connected to host services.
        deny("socketpair", Compare(0, 1, 1, 0))
        for kind in range(16):
            if kind not in {1, 5}:
                deny("socketpair", Compare(1, 7, 0xF, kind))
        # Only AF_INET / AF_INET6, SOCK_STREAM, protocol default or TCP.
        deny("socket", Compare(0, 3, 1, 0))
        deny("socket", Compare(0, 5, 11, 0))
        for domain in range(3, 10):
            deny("socket", Compare(0, 4, domain, 0))
        for kind in range(16):
            if kind != 1:
                deny("socket", Compare(1, 7, 0xF, kind))
        deny("socket", Compare(2, 6, 6, 0))
        for protocol in range(1, 6):
            deny("socket", Compare(2, 4, protocol, 0))
        if sec.seccomp_load(ctx) != 0:
            raise RuntimeError("cannot enforce chat seccomp")
    finally:
        sec.seccomp_release(ctx)


def main() -> None:
    config = json.loads(Path(sys.argv[1]).read_text())
    args = sys.argv[2:]
    # The SDK creates its schema in a separate /tmp directory. Copy this exact
    # trusted argument before confinement rather than exposing /tmp globally.
    if "--output-schema" in args:
        index = args.index("--output-schema") + 1
        target = Path(config["runtime"]) / "output-schema.json"
        shutil.copyfile(args[index], target)
        args[index] = str(target)
    # Keep an unconfined, secret-free supervisor so tools that fork or detach
    # are reparented here and reaped before the SDK process reports completion.
    # Only the child CLI and all of its tools inherit the confinement policy.
    libc = ctypes.CDLL(None, use_errno=True)
    _checked(libc.prctl(36, 1, 0, 0, 0))  # PR_SET_CHILD_SUBREAPER
    (Path(config["runtime"]) / ".chat-cli-pid").write_text(str(os.getpid()))
    child = os.fork()
    if child == 0:
        os.chdir(config["work"])
        confine(read_paths=config["read_paths"], write_paths=config["write_paths"], ports=config["ports"])
        # CLI 0.152.1 repairs newly created file modes with fchmod(..., 0644)
        # when it inherits the service's 0077 umask. Metadata syscalls remain
        # denied because Landlock does not scope them. Set the compatible mask
        # only after fork, inside the already confined child: parent/supervisor
        # keep 0077, and both outer turn directories remain private (0700).
        os.umask(0o022)
        os.execve(config["cli"], [config["cli"], *args], dict(os.environ))
    _pid, status = os.waitpid(child, 0)
    # After the CLI exits, its orphaned descendants become our children. Kill
    # and reap each generation; never leave a background tool running.
    children_file = Path(f"/proc/self/task/{os.getpid()}/children")
    while True:
        children = [int(value) for value in children_file.read_text().split()]
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.waitpid(-1, 0)
        except ChildProcessError:
            break
    code = os.waitstatus_to_exitcode(status)
    raise SystemExit(code if code >= 0 else 128 - code)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Fixed diagnostics: do not echo paths/configuration/credentials.
        print("AiQQ chat isolation failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(126)
