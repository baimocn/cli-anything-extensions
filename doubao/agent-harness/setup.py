#!/usr/bin/env python3
"""Setup script for cli-anything-doubao — a Doubao desktop harness CLI.

Install (dev mode):
    pip install -e .

The CDP runtime dependency (websocket-client) is required only by the real
backend; tests mock the boundary and run without it.
"""

from pathlib import Path
from setuptools import setup, find_namespace_packages

ROOT = Path(__file__).parent
README = ROOT / "cli_anything/doubao/README.md"
long_description = README.read_text(encoding="utf-8") if README.exists() else ""

setup(
    name="cli-anything-doubao",
    version="1.0.0",
    description="CLI harness for the Doubao desktop app (Chromium shell) via the Chrome DevTools Protocol",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/baimocn/cli-anything-extensions",
    license="MIT",
    packages=find_namespace_packages(include=("cli_anything.*",)),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.1",
        "prompt-toolkit>=3.0",
        "websocket-client>=1.6",
    ],
    extras_require={"dev": ["pytest>=7", "pytest-cov>=4"]},
    entry_points={
        "console_scripts": [
            "cli-anything-doubao=cli_anything.doubao.doubao_cli:main",
        ],
    },
    package_data={"cli_anything.doubao": ["skills/*.md"]},
    include_package_data=True,
    zip_safe=False,
    keywords=["cli", "doubao", "cdp", "devtools", "bytedance", "cli-anything"],
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
