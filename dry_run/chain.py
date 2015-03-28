from tasks.simple import (AddNumbers, DivideNumbers, MultiplyNumbers,
                          DivisionByZero)
from cloudferrylib.scheduler import cursor
from cloudferrylib.scheduler import scheduler


def process_test_chain(redis=None):
    chain = (AddNumbers(1, 2) >> DivideNumbers(4, 3) >>
             MultiplyNumbers(4, 2) >> DivisionByZero() >> DivisionByZero() >>
             DivisionByZero() >> DivideNumbers(4, 3) >> DivideNumbers(4, 3))
    scheduler.configured_scheduler(redis=redis,
                                   migration=cursor.Cursor(chain)).start()
