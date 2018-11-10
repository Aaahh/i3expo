from threading import Thread

class ExThread(Thread):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._real_run = self.run
        self.run = self._wrap_run
        self.crashed = False

    def _wrap_run(self):
        try:
            self._real_run()
        except:
            self.crashed = True
            raise
