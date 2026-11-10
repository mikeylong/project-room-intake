#!/usr/bin/env python3
"""Instrument selected Python filesystem APIs while running the smoke scanner.

This is a regression-test aid for uniquely named synthetic sources, not an OS
sandbox. The JSON policy names source basenames and the exact-file parent.
"""
from __future__ import annotations

import argparse
import builtins
import io
import json
import os
import runpy
import stat
import sys
import traceback


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("scanner_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    scanner_args = args.scanner_args
    if scanner_args[:1] == ["--"]:
        scanner_args = scanner_args[1:]

    original = {
        "builtins.open": builtins.open,
        "io.open": io.open,
        "os.open": os.open,
        "os.stat": os.stat,
        "os.lstat": os.lstat,
        "os.scandir": os.scandir,
        "os.listdir": os.listdir,
        "os.fstat": os.fstat,
    }
    with original["builtins.open"](args.config, encoding="utf-8") as handle:
        config = json.load(handle)
    source_names = set(config["source_names"])
    permitted_names = set(config["permitted_content_names"])
    forbidden_names = set(config["forbidden_stat_names"])
    exact_parent = os.path.abspath(os.fsdecode(config["exact_parent"]))
    scanner_path = os.path.join(config["installed"], "scripts", "scan_sources.py")
    scanner_dir = os.path.dirname(scanner_path)
    parent_identity = None
    try:
        parent_stat = original["os.stat"](exact_parent)
        parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
    except OSError:
        # An unavailable-input scenario can deliberately remove this parent.
        pass

    record = {
        "instrumentation": "selected Python APIs; not OS tracing",
        "source_content_open_attempts": [],
        "forbidden_access_attempts": [],
        "scanner_exit": None,
        "scanner_error": None,
    }

    def path_info(value):
        if isinstance(value, int):
            return {"fd": value}, None, None
        if value is None:
            value = "."
        decoded = os.fsdecode(os.fspath(value))
        return {"path": decoded}, os.path.basename(os.path.normpath(decoded)), decoded

    def deny(api, details, reason):
        event = {"api": api, **details, "reason": reason}
        record["forbidden_access_attempts"].append(event)
        raise PermissionError("Instrumentation refused access: " + repr(event))

    def is_source_symlink(path, kwargs):
        lstat_kwargs = {"dir_fd": kwargs["dir_fd"]} if "dir_fd" in kwargs else {}
        try:
            return stat.S_ISLNK(original["os.lstat"](path, **lstat_kwargs).st_mode)
        except OSError:
            return False

    def content_check(api, value, directory=False, kwargs=None):
        details, name, _ = path_info(value)
        if name not in source_names and name not in forbidden_names:
            return
        # Hash permission does not authorize following a substituted symlink.
        symlink = is_source_symlink(value, kwargs or {})
        if directory and not symlink:
            return
        permitted = name in permitted_names and name not in forbidden_names and not symlink
        event = {"api": api, **details, "name": name, "permitted": permitted}
        record["source_content_open_attempts"].append(event)
        if not permitted:
            deny(api, details, "source content is not permitted")

    def guarded_builtin_open(file, *open_args, **open_kwargs):
        content_check("builtins.open", file)
        return original["builtins.open"](file, *open_args, **open_kwargs)

    def guarded_io_open(file, *open_args, **open_kwargs):
        content_check("io.open", file)
        return original["io.open"](file, *open_args, **open_kwargs)

    def guarded_os_open(path, flags, *open_args, **open_kwargs):
        content_check("os.open", path, bool(flags & getattr(os, "O_DIRECTORY", 0)), open_kwargs)
        return original["os.open"](path, flags, *open_args, **open_kwargs)

    def stat_check(api, path, stat_kwargs, follows):
        details, name, _ = path_info(path)
        if isinstance(path, int):
            return
        if name in forbidden_names:
            deny(api, details, "forbidden source metadata inspection")
        if follows and name in source_names and is_source_symlink(path, stat_kwargs):
            deny(api, details, "following a source symlink")

    def guarded_stat(path, *stat_args, **stat_kwargs):
        stat_check("os.stat", path, stat_kwargs, stat_kwargs.get("follow_symlinks", True))
        return original["os.stat"](path, *stat_args, **stat_kwargs)

    def guarded_lstat(path, *stat_args, **stat_kwargs):
        stat_check("os.lstat", path, stat_kwargs, False)
        return original["os.lstat"](path, *stat_args, **stat_kwargs)

    def enumeration_check(api, path):
        details, _, decoded = path_info(path)
        is_parent = False
        if isinstance(path, int):
            if parent_identity is not None:
                try:
                    result = original["os.fstat"](path)
                    is_parent = (result.st_dev, result.st_ino) == parent_identity
                except OSError:
                    pass
        else:
            is_parent = os.path.abspath(decoded) == exact_parent
            if not is_parent and parent_identity is not None:
                try:
                    result = original["os.stat"](decoded)
                    is_parent = (result.st_dev, result.st_ino) == parent_identity
                except OSError:
                    pass
        if is_parent:
            deny(api, details, "enumeration of the exact file parent is forbidden")

    def guarded_scandir(path="."):
        enumeration_check("os.scandir", path)
        return original["os.scandir"](path)

    def guarded_listdir(path="."):
        enumeration_check("os.listdir", path)
        return original["os.listdir"](path)

    saved_argv = sys.argv
    saved_path = sys.path[:]
    saved_bytecode = sys.dont_write_bytecode
    exit_code = 0
    try:
        builtins.open = guarded_builtin_open
        io.open = guarded_io_open
        os.open = guarded_os_open
        os.stat = guarded_stat
        os.lstat = guarded_lstat
        os.scandir = guarded_scandir
        os.listdir = guarded_listdir
        sys.dont_write_bytecode = True
        sys.path.insert(0, scanner_dir)
        sys.argv = [scanner_path] + scanner_args
        try:
            runpy.run_path(scanner_path, run_name="__main__")
        except SystemExit as exc:
            exit_code = 0 if exc.code is None else exc.code
            if not isinstance(exit_code, int):
                print(exit_code, file=sys.stderr)
                exit_code = 1
        except BaseException as exc:
            exit_code = 130 if isinstance(exc, KeyboardInterrupt) else 1
            record["scanner_error"] = {"type": type(exc).__name__, "message": str(exc)}
            traceback.print_exc()
        record["scanner_exit"] = exit_code
    finally:
        builtins.open = original["builtins.open"]
        io.open = original["io.open"]
        os.open = original["os.open"]
        os.stat = original["os.stat"]
        os.lstat = original["os.lstat"]
        os.scandir = original["os.scandir"]
        os.listdir = original["os.listdir"]
        sys.argv = saved_argv
        sys.path[:] = saved_path
        sys.dont_write_bytecode = saved_bytecode
        record["guard_failed"] = bool(record["forbidden_access_attempts"])
        # A scanner catching PermissionError must not hide a guard violation.
        record["runner_exit"] = 97 if record["guard_failed"] else exit_code
        os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
        with original["builtins.open"](args.log, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
    return record["runner_exit"]


if __name__ == "__main__":
    sys.exit(main())
