# This doesn't get called when unittest imports our test classes (but
# it does when we import the test classes ourselves). Don't know why.
from ferenda.manager import setup_logger; setup_logger('CRITICAL')
