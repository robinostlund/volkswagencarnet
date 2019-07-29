from setuptools import setup

setup(
    name = 'volkswagencarnet',
    version = '4.0.22',
    description = 'Communicate with Volkswagen Carnet',
    author = 'Robin Ostlund',
    author_email = 'me@robinostlund.name',
    url = 'https://github.com/robinostlund/volkswagencarnet', # use the URL to the github repo
    #download_url = 'https://github.com/robinostlund/volkswagencarnet/archive/0.1.tar.gz', # I'll explain this in a second
    download_url = 'https://github.com/robinostlund/volkswagencarnet/archive/4.0.22.tar.gz',
    py_modules=[
        "volkswagencarnet",
        "dashboard",
        "utilities",
        "__init__"
    ],
    provides=["volkswagencarnet"],
    install_requires=[
        'requests',
        'beautifulsoup4'
    ]
)
