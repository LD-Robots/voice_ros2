from setuptools import find_packages
from setuptools import setup

setup(
    name='conversational_interfaces',
    version='1.0.0',
    packages=find_packages(
        include=('conversational_interfaces', 'conversational_interfaces.*')),
)
