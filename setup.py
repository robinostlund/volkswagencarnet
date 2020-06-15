from setuptools import setup

setup(
    name='volkswagencarnet',
    version='4.1.31',
    description='Communicate with Volkswagen Carnet',
    author='Robin Ostlund',
    author_email='me@robinostlund.name',
    url='https://github.com/robinostlund/volkswagencarnet',
    download_url='https://github.com/robinostlund/volkswagencarnet/archive/4.1.31.tar.gz',
    py_modules=[
        "volkswagencarnet",
        "dashboard",
        "utilities",
        "__init__"
    ],
    provides=["volkswagencarnet"],
    install_requires=[
        'requests',
        'lxml',
        'beautifulsoup4'
    ]
)
