#!/usr/bin/env python3
"""
Python environment setup helper — works on Windows, Linux, and macOS.

Creates a virtual environment (.venv) inside the target skill directory
and installs packages from requirements.txt if present.

Usage:
    python setup_python_env.py <skill_dir> [--requirements <path>]

Arguments:
    skill_dir               Path to the skill directory (default: current directory)
    --requirements <path>   Path to requirements.txt (default: <skill_dir>/requirements.txt)
    --force                 Re-create .venv even if it already exists

Exit codes:
    0  Success
    1  Error (message printed to stderr)
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def find_python() -> str:
    """Return the current interpreter path (used for venv creation)."""
    return sys.executable


def venv_python(venv_dir: Path) -> Path:
    """Return the path to the Python binary inside the venv."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def venv_pip(venv_dir: Path) -> Path:
    """Return the path to the pip binary inside the venv."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "pip.exe"
    return venv_dir / "bin" / "pip"


def create_venv(venv_dir: Path, force: bool) -> None:
    if venv_dir.exists() and not force:
        print(f"[setup_python_env] .venv already exists at {venv_dir} — skipping creation.")
        return
    print(f"[setup_python_env] Creating virtual environment at {venv_dir} …")
    subprocess.run(
        [find_python(), "-m", "venv", str(venv_dir)],
        check=True,
    )
    print(f"[setup_python_env] Virtual environment created.")


def install_requirements(venv_dir: Path, requirements: Path) -> None:
    if not requirements.exists():
        print(f"[setup_python_env] No requirements.txt found at {requirements} — skipping install.")
        return
    pip = venv_pip(venv_dir)
    print(f"[setup_python_env] Installing packages from {requirements} …")
    subprocess.run(
        [str(pip), "install", "--upgrade", "-r", str(requirements)],
        check=True,
    )
    print(f"[setup_python_env] Packages installed.")


def print_activation_hint(venv_dir: Path) -> None:
    if sys.platform == "win32":
        activate = venv_dir / "Scripts" / "Activate.ps1"
        print(f"\n[setup_python_env] To activate (PowerShell): . '{activate}'")
        activate_cmd = venv_dir / "Scripts" / "activate.bat"
        print(f"[setup_python_env] To activate (CMD):        {activate_cmd}")
    else:
        activate = venv_dir / "bin" / "activate"
        print(f"\n[setup_python_env] To activate: source '{activate}'")
    python_bin = venv_python(venv_dir)
    print(f"[setup_python_env] Python binary: {python_bin}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up a Python virtual environment for a skill directory."
    )
    parser.add_argument(
        "skill_dir",
        nargs="?",
        default=".",
        help="Path to the skill directory (default: current directory)",
    )
    parser.add_argument(
        "--requirements",
        default=None,
        help="Path to requirements.txt (default: <skill_dir>/requirements.txt)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-create the .venv even if it already exists",
    )
    args = parser.parse_args()

    skill_dir = Path(args.skill_dir).resolve()
    if not skill_dir.is_dir():
        print(f"Error: '{skill_dir}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    venv_dir = skill_dir / ".venv"
    requirements = Path(args.requirements).resolve() if args.requirements else skill_dir / "requirements.txt"

    try:
        create_venv(venv_dir, force=args.force)
        install_requirements(venv_dir, requirements)
        print_activation_hint(venv_dir)
        print("\n[setup_python_env] Done.")
    except subprocess.CalledProcessError as exc:
        print(f"Error: command failed with exit code {exc.returncode}.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
