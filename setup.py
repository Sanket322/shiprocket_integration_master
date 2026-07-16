from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

setup(
    name="shiprocket_integration",
    version="1.0.0",
    description="Shiprocket Shipping Integration for ERPNext v16",
    author="Reformiqo",
    author_email="hello@reformiqo.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
