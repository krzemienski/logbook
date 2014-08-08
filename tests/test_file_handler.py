import os
import pytest
from datetime import datetime

import logbook
from logbook.helpers import u, xrange

from .utils import capturing_stderr_context, LETTERS


def test_file_handler(logfile, activation_strategy, logger):
    handler = logbook.FileHandler(logfile,
                                  format_string='{record.level_name}:{record.channel}:'
                                  '{record.message}',)
    with activation_strategy(handler):
        logger.warn('warning message')
    handler.close()
    with open(logfile) as f:
        assert f.readline() == 'WARNING:testlogger:warning message\n'


def test_file_handler_unicode(logfile, activation_strategy, logger):
    with capturing_stderr_context() as captured:
        with activation_strategy(logbook.FileHandler(logfile)):
            logger.info(u('\u0431'))
    assert (not captured.getvalue())


def test_file_handler_delay(logfile, activation_strategy, logger):
    handler = logbook.FileHandler(logfile,
                                  format_string='{record.level_name}:{record.channel}:'
                                  '{record.message}', delay=True)
    assert (not os.path.isfile(logfile))
    with activation_strategy(handler):
        logger.warn('warning message')
    handler.close()

    with open(logfile) as f:
        assert f.readline() == 'WARNING:testlogger:warning message\n'


def test_monitoring_file_handler(logfile, activation_strategy, logger):
    if os.name == 'nt':
        pytest.skip('unsupported on windows due to different IO (also unneeded)')
    handler = logbook.MonitoringFileHandler(logfile,
                                            format_string='{record.level_name}:{record.channel}:'
                                            '{record.message}', delay=True)
    with activation_strategy(handler):
        logger.warn('warning message')
        os.rename(logfile, logfile + '.old')
        logger.warn('another warning message')
    handler.close()
    with open(logfile) as f:
        assert f.read().strip() == 'WARNING:testlogger:another warning message'


def test_custom_formatter(activation_strategy, logfile, logger):
    def custom_format(record, handler):
        return record.level_name + ':' + record.message

    handler = logbook.FileHandler(logfile)
    with activation_strategy(handler):
        handler.formatter = custom_format
        logger.warn('Custom formatters are awesome')

    with open(logfile) as f:
        assert f.readline() == 'WARNING:Custom formatters are awesome\n'


def test_rotating_file_handler(logfile, activation_strategy, logger):
    basename = os.path.basename(logfile)
    handler = logbook.RotatingFileHandler(logfile, max_size=2048,
                                          backup_count=3,
                                          )
    handler.format_string = '{record.message}'
    with activation_strategy(handler):
        for c, x in zip(LETTERS, xrange(32)):
            logger.warn(c * 256)
    files = [x for x in os.listdir(os.path.dirname(logfile))
             if x.startswith(basename)]
    files.sort()

    assert files == [basename, basename + '.1', basename + '.2', basename + '.3']
    with open(logfile) as f:
        assert f.readline().rstrip() == ('C' * 256)
        assert f.readline().rstrip() == ('D' * 256)
        assert f.readline().rstrip() == ('E' * 256)
        assert f.readline().rstrip() == ('F' * 256)


def test_timed_rotating_file_handler(tmpdir, activation_strategy):
    basename = str(tmpdir.join('trot.log'))
    handler = logbook.TimedRotatingFileHandler(basename, backup_count=3)
    handler.format_string = '[{record.time:%H:%M}] {record.message}'

    def fake_record(message, year, month, day, hour=0,
                    minute=0, second=0):
        lr = logbook.LogRecord('Test Logger', logbook.WARNING,
                               message)
        lr.time = datetime(year, month, day, hour, minute, second)
        return lr

    with activation_strategy(handler):
        for x in xrange(10):
            handler.handle(fake_record('First One', 2010, 1, 5, x + 1))
        for x in xrange(20):
            handler.handle(fake_record('Second One', 2010, 1, 6, x + 1))
        for x in xrange(10):
            handler.handle(fake_record('Third One', 2010, 1, 7, x + 1))
        for x in xrange(20):
            handler.handle(fake_record('Last One', 2010, 1, 8, x + 1))

    files = sorted(x for x in os.listdir(str(tmpdir)) if x.startswith('trot'))

    assert files == ['trot-2010-01-06.log', 'trot-2010-01-07.log', 'trot-2010-01-08.log']
    with open(str(tmpdir.join('trot-2010-01-08.log'))) as f:
        assert f.readline().rstrip() == '[01:00] Last One'
        assert f.readline().rstrip() == '[02:00] Last One'
    with open(str(tmpdir.join('trot-2010-01-07.log'))) as f:
        assert f.readline().rstrip() == '[01:00] Third One'
        assert f.readline().rstrip() == '[02:00] Third One'
