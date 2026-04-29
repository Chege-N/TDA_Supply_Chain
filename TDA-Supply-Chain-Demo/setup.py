from setuptools import setup, find_packages

setup(
    name="tda_supply_chain",
    version="1.0.0",
    description="Real-time topological anomaly detection for global supply chains",
    author="Felix Chege N.",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "numpy>=1.24.0",
        "scipy>=1.10.0",
        "fastapi>=0.100.0",
        "uvicorn[standard]>=0.23.0",
        "pydantic>=2.0.0",
        "matplotlib>=3.7.0",
    ],
    extras_require={
        "redis": ["redis>=4.6.0"],
        "dev": ["pytest>=7.4.0", "pytest-cov>=4.1.0", "mypy>=1.5.0"],
        "notebook": ["jupyter>=1.0.0", "ipywidgets>=8.0.0"],
    },
    entry_points={"console_scripts": ["tda-supply-chain=main:main"]},
)
