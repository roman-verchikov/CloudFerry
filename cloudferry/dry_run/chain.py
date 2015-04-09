from tasks.simple import (AddNumbers, DivideNumbers, MultiplyNumbers,
                          DivisionByZero)
from cloudferry.lib.scheduler import cursor
from cloudferry.lib.scheduler import scheduler


def process_test_chain():
    chain = (AddNumbers(1, 2) >> DivideNumbers(4, 3) >>
             MultiplyNumbers(4, 2) >> DivisionByZero())
    scheduler.Scheduler(migration=cursor.Cursor(chain)).start()
