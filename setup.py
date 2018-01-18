from distutils.core import setup
setup(
    name = 'volkswagencarnet',
    version = '0.3.2',
    description = 'Communicate with Volkswagen Carnet',
    author = 'Robin Ostlund',
    author_email = 'me@robinostlund.name',
    url = 'https://github.com/robinostlund/volkswagencarnet', # use the URL to the github repo
    #download_url = 'https://github.com/robinostlund/volkswagencarnet/archive/0.1.tar.gz', # I'll explain this in a second
    py_modules=["volkswagencarnet"],
    provides=["volkswagencarnet"],
    install_requires=[
        'requests',
    ],
)
