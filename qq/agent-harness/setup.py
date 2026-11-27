#!/usr/bin/env python3
"""Setup script for cli-anything-qq — a OneBot 11 / NapCat harness for QQ.

Install (dev mode):
    pip install -e .
"""

from pathlib import Path
from setuptools import setup, find_namespace_packages

ROOT = Path(__file__).parent
README = ROOT / "cli_anything/qq/README.md"
long_description = README.read_text(encoding="utf-8") if README.exists() else ""

setup(
    name="cli-anything-qq",
    version="1.0.0",
    description="CLI harness for QQ via NapCat / OneBot 11 HTTP action API",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/baimocn/cli-anything-extensions",
    license="MIT",
    packages=find_namespace_packages(include=("cli_anything.*",)),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.1",
        "prompt-toolkit>=3.0",
        "requests>=2.28",
    ],
    extras_require={"dev": ["pytest>=7", "pytest-cov>=4"]},
    entry_points={
        "console_scripts": [
            "cli-anything-qq=cli_anything.qq.qq_cli:main",
        ],
    },
    package_data={"cli_anything.qq": ["skills/*.md"]},
    include_package_data=True,
    zip_safe=False,
    keywords=["cli", "qq", "napcat", "onebot", "cli-anything"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
