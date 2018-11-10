import time
import logging

LOG = logging.getLogger('debo')

def debounce(s):
    def decorate(f):
        t = None

        def wrapped(*args, **kwargs):
            nonlocal t
            t_ = time.time()
            if t is None or t_ - t >= s:
                result = f(*args, **kwargs)
                t = time.time()
                return result
            else:
                LOG.info('Debounced call to ' + f.__name__)
        return wrapped
    return decorate
