#!/usr/bin/env python3
"""Setup script for cli-anything-wx — a WeChat 4.x harness CLI.

Install (dev mode):
    pip install -e .

The heavy runtime dependency (wechatauto-replica, comtypes, pywin32) is
optional on purpose: every command that needs it goes through
``utils.wx_backend``, which raises a clear RuntimeError when the library is
missing. Tests run fully mocked without it.
"""

from pathlib import Path
from setuptools import setup, find_namespace_packages

ROOT = Path(__file__).parent
README = ROOT / "cli_anything/wx/README.md"
long_description = README.read_text(encoding="utf-8") if README.exists() else ""

setup(
    name="cli-anything-wx",
    version="1.0.0",
    description="CLI harness for WeChat PC 4.x — decrypted-DB reading + UIA sending via wechatauto",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/baimocn/cli-anything-extensions",
    license="MIT",
    packages=find_namespace_packages(include=("cli_anything.*",)),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.1",
        "prompt-toolkit>=3.0",
    ],
    extras_require={
        "dev": ["pytest>=7", "pytest-cov>=4"],
        # Real-machine control surface (WeChat 4.x, Windows only):
        "real": [
            "wechatauto-replica>=1.2.10",
            "comtypes>=1.4",
            "pywin32>=306",
        ],
    },
    entry_points={
        "console_scripts": [
            "cli-anything-wx=cli_anything.wx.wx_cli:main",
        ],
    },
    package_data={"cli_anything.wx": ["skills/*.md"]},
    include_package_data=True,
    zip_safe=False,
    keywords=["cli", "wechat", "weixin", "wechatauto", "uia", "cli-anything"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: Microsoft :: Windows",
    ],
)
