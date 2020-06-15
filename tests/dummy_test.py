import pytest
import sys
import os

# we need to change os path to be able to import volkswagecarnet
myPath = os.path.dirname(os.path.abspath(__file__))
print(myPath)
sys.path.insert(0, myPath + '/../')


def test_volkswagencarnet():
    import volkswagencarnet
    vw = volkswagencarnet.Connection('test@test.com', 'mypassword')

    if not vw.logged_in:
        return True
    pytest.fail('Something happend we should have got a False from vw.logged_in')
