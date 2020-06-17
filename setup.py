from setuptools import setup

# read the contents of your README file
from os import path
this_directory = path.abspath(path.dirname(__file__))
with open(path.join(this_directory, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='volkswagencarnet',
    description='Communicate with Volkswagen WeConnect',
    author='Robin Ostlund',
    author_email='me@robinostlund.name',
    url='https://github.com/robinostlund/volkswagencarnet',
    long_description=long_description,
    long_description_content_type='text/markdown',
    py_modules=[
        "volkswagencarnet",
        "dashboard",
        "utilities",
        "__init__"
    ],
    provides=["volkswagencarnet"],
    install_requires=list(open("requirements.txt").read().strip().split("\n")),
    use_scm_version=True,
    setup_requires=[
        'setuptools_scm',
        'pytest>=5,<6',
    ]
)
