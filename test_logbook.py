# -*- coding: utf-8 -*-
from __future__ import division

import logbook

import os
import re
import sys
import time
import thread
import pickle
import shutil
import unittest
import tempfile
import string
import socket
from datetime import datetime, timedelta
from random import randrange
from itertools import izip
from cStringIO import StringIO
from logbook.helpers import json


_skipped_modules = []
_missing = object()


class capture_stderr(object):
    def __init__(self):
        self.old = sys.stderr
        self.new = StringIO()
        self.current = self.old

    def start(self):
        sys.stderr = self.current = self.new = StringIO()

    def end(self):
        sys.stderr = self.current = self.old

    def __enter__(self):
        self.start()
        return sys.stderr

    def __exit__(self, etype, evalue, tb):
        self.end()
capture_stderr = capture_stderr()


def require(name):
    def decorate(f):
        if name in _skipped_modules:
            return None
        try:
            __import__(name)
        except ImportError:
            _skipped_modules.append(name)
            return None
        return f
    return decorate


def missing(name):
    def decorate(f):
        def wrapper(*args, **kwargs):
            old = sys.modules.get(name, _missing)
            sys.modules[name] = None
            try:
                f(*args, **kwargs)
            finally:
                if old is _missing:
                    del sys.modules[name]
                else:
                    sys.modules[name] = old
        return wrapper
    return decorate


def make_fake_mail_handler(**kwargs):
    class FakeMailHandler(logbook.MailHandler):
        mails = []

        def get_connection(self):
            return self

        def close_connection(self, con):
            pass

        def sendmail(self, fromaddr, recipients, mail):
            self.mails.append((fromaddr, recipients, mail))

    return FakeMailHandler('foo@example.com', ['bar@example.com'],
                           level=logbook.ERROR, **kwargs)


class LogbookTestCase(unittest.TestCase):

    def setUp(self):
        self.log = logbook.Logger('testlogger')


class BasicAPITestCase(LogbookTestCase):

    def test_basic_logging(self):
        handler = logbook.TestHandler()
        handler.push_application()
        try:
            self.log.warn('This is a warning.  Nice hah?')
        finally:
            handler.pop_application()

        self.assert_(handler.has_warning('This is a warning.  Nice hah?'))
        self.assertEqual(handler.formatted_records, [
            '[WARNING] testlogger: This is a warning.  Nice hah?'
        ])

    def test_extradict(self):
        handler = logbook.TestHandler()
        handler.push_application()
        try:
            self.log.warn('Test warning')
        finally:
            handler.pop_application()
        record = handler.records[0]
        record.extra['existing'] = 'foo'
        self.assertEqual(record.extra['nonexisting'], '')
        self.assertEqual(record.extra['existing'], 'foo')
        self.assertEqual(repr(record.extra),
                         'ExtraDict({\'existing\': \'foo\'})')

    def test_lookup_helpers(self):
        self.assertRaises(LookupError, logbook.get_level_name, 37)
        self.assertRaises(LookupError, logbook.lookup_level, 'FOO')

    def test_custom_logger(self):
        client_ip = '127.0.0.1'

        class CustomLogger(logbook.Logger):
            def process_record(self, record):
                record.extra['ip'] = client_ip

        custom_log = CustomLogger('awesome logger')
        fmt = '[{record.level_name}] {record.channel}: ' \
              '{record.message} [{record.extra[ip]}]'
        handler = logbook.TestHandler(format_string=fmt)
        self.assertEqual(handler.format_string, fmt)

        handler.push_thread()
        try:
            custom_log.warn('Too many sounds')
            self.log.warn('"Music" playing')
        finally:
            handler.pop_thread()

        self.assertEqual(handler.formatted_records, [
            '[WARNING] awesome logger: Too many sounds [127.0.0.1]',
            '[WARNING] testlogger: "Music" playing []'
        ])

    def test_handler_exception(self):
        class ErroringHandler(logbook.TestHandler):
            def emit(self, record):
                raise RuntimeError('something bad happened')

        handler = ErroringHandler()
        capture_stderr.start()
        stderr = capture_stderr.current
        try:
            handler.push_application()
            try:
                self.log.warn('I warn you.')
            finally:
                handler.pop_application()
            self.assert_('something bad happened' in stderr.getvalue())
            self.assert_('I warn you' not in stderr.getvalue())
        finally:
            capture_stderr.end()

    def test_formatting_exception(self):
        def make_record():
            return logbook.LogRecord('Test Logger', logbook.WARNING,
                                     'Hello {foo:invalid}',
                                     kwargs={'foo': 42},
                                     frame=sys._getframe())
        record = make_record()
        try:
            record.message
        except TypeError, e:
            errormsg = str(e)
        else:
            self.assertFalse('Expected exception')

        self.assert_('Could not format message with provided arguments: '
                     'Invalid conversion specification' in errormsg)
        self.assert_("msg='Hello {foo:invalid}'" in errormsg)
        self.assert_('args=()' in errormsg)
        self.assert_("kwargs={'foo': 42}" in errormsg)
        self.assert_(re.search('Happened in file .*test_logbook.py, '
                               'line \d+', errormsg))

    def test_exception_catching(self):
        logger = logbook.Logger('Test')
        handler = logbook.TestHandler()
        handler.push_thread()
        try:
            self.assertFalse(handler.has_error())
            try:
                1 / 0
            except Exception:
                logger.exception()
            try:
                1 / 0
            except Exception:
                logger.exception('Awesome')
            self.assert_(handler.has_error('Uncaught exception occurred'))
            self.assert_(handler.has_error('Awesome'))
        finally:
            handler.pop_thread()
        self.assert_(handler.records[0].exc_info is not None)
        self.assert_('1 / 0' in handler.records[0].formatted_exception)

    def test_exporting(self):
        handler = logbook.TestHandler()
        handler.push_application()
        try:
            try:
                1 / 0
            except Exception:
                self.log.exception()
            record = handler.records[0]
        finally:
            handler.pop_application()

        exported = record.to_dict()
        record.close()
        imported = logbook.LogRecord.from_dict(exported)
        for key, value in record.__dict__.iteritems():
            if key[0] == '_':
                continue
            self.assertEqual(value, getattr(imported, key))

    def test_pickle(self):
        handler = logbook.TestHandler()
        handler.push_application()
        try:
            try:
                1 / 0
            except Exception:
                self.log.exception()
            record = handler.records[0]
        finally:
            handler.pop_application()
        record.pull_information()
        record.close()

        for p in xrange(pickle.HIGHEST_PROTOCOL):
            exported = pickle.dumps(record, p)
            imported = pickle.loads(exported)
            for key, value in record.__dict__.iteritems():
                if key[0] == '_':
                    continue
                self.assertEqual(value, getattr(imported, key))


class HandlerTestCase(LogbookTestCase):

    def setUp(self):
        LogbookTestCase.setUp(self)
        self.dirname = tempfile.mkdtemp()
        self.filename = os.path.join(self.dirname, 'log.tmp')

    def tearDown(self):
        shutil.rmtree(self.dirname)
        LogbookTestCase.tearDown(self)

    def test_file_handler(self):
        handler = logbook.FileHandler(self.filename,
            format_string='{record.level_name}:{record.channel}:'
            '{record.message}',)
        handler.push_thread()
        try:
            self.log.warn('warning message')
        finally:
            handler.pop_thread()
        handler.close()
        f = open(self.filename)
        try:
            self.assertEqual(f.readline(),
                             'WARNING:testlogger:warning message\n')
        finally:
            f.close()

    def test_file_handler_delay(self):
        handler = logbook.FileHandler(self.filename,
            format_string='{record.level_name}:{record.channel}:'
            '{record.message}', delay=True)
        self.assertFalse(os.path.isfile(self.filename))
        handler.push_thread()
        try:
            self.log.warn('warning message')
        finally:
            handler.pop_thread()
        handler.close()
        f = open(self.filename)
        try:
            self.assertEqual(f.readline(),
                             'WARNING:testlogger:warning message\n')
        finally:
            f.close()

    def test_monitoring_file_handler(self):
        handler = logbook.MonitoringFileHandler(self.filename,
            format_string='{record.level_name}:{record.channel}:'
            '{record.message}', delay=True)
        handler.push_thread()
        try:
            self.log.warn('warning message')
            os.rename(self.filename, self.filename + '.old')
            self.log.warn('another warning message')
        finally:
            handler.pop_thread()
        handler.close()
        f = open(self.filename)
        try:
            self.assertEqual(f.read().strip(),
                             'WARNING:testlogger:another warning message')
        finally:
            f.close()

    # unsupported on windows due to different IO (also unneeded)
    if os.name == 'nt':
        del test_monitoring_file_handler

    def test_custom_formatter(self):
        def custom_format(record, handler):
            return record.level_name + ':' + record.message
        handler = logbook.FileHandler(self.filename)
        handler.push_application()
        try:
            handler.formatter = custom_format
            self.log.warn('Custom formatters are awesome')
        finally:
            handler.pop_application()
        f = open(self.filename)
        try:
            self.assertEqual(f.readline(),
                             'WARNING:Custom formatters are awesome\n')
        finally:
            f.close()

    def test_rotating_file_handler(self):
        basename = os.path.join(self.dirname, 'rot.log')
        handler = logbook.RotatingFileHandler(basename, max_size=2048,
                                              backup_count=3,
                                              )
        handler.format_string = '{record.message}'
        handler.push_application()
        try:
            for c, x in izip(string.letters, xrange(32)):
                self.log.warn(c * 256)
        finally:
            handler.pop_application()
        files = [x for x in os.listdir(self.dirname)
                 if x.startswith('rot.log')]
        files.sort()

        self.assertEqual(files, ['rot.log', 'rot.log.1', 'rot.log.2',
                                 'rot.log.3'])
        f = open(basename)
        try:
            self.assertEqual(f.readline().rstrip(), 'C' * 256)
            self.assertEqual(f.readline().rstrip(), 'D' * 256)
            self.assertEqual(f.readline().rstrip(), 'E' * 256)
            self.assertEqual(f.readline().rstrip(), 'F' * 256)
        finally:
            f.close()

    def test_timed_rotating_file_handler(self):
        basename = os.path.join(self.dirname, 'trot.log')
        handler = logbook.TimedRotatingFileHandler(basename, backup_count=3)
        handler.format_string = '[{record.time:%H:%M}] {record.message}'

        def fake_record(message, year, month, day, hour=0,
                        minute=0, second=0):
            lr = logbook.LogRecord('Test Logger', logbook.WARNING,
                                   message)
            lr.time = datetime(year, month, day, hour, minute, second)
            return lr

        handler.push_application()
        try:
            for x in xrange(10):
                handler.handle(fake_record('First One', 2010, 1, 5, x + 1))
            for x in xrange(20):
                handler.handle(fake_record('Second One', 2010, 1, 6, x + 1))
            for x in xrange(10):
                handler.handle(fake_record('Third One', 2010, 1, 7, x + 1))
            for x in xrange(20):
                handler.handle(fake_record('Last One', 2010, 1, 8, x + 1))
        finally:
            handler.pop_application()

        files = [x for x in os.listdir(self.dirname) if x.startswith('trot')]
        files.sort()
        self.assertEqual(files, ['trot-2010-01-06.log', 'trot-2010-01-07.log',
                                 'trot-2010-01-08.log'])
        f = open(os.path.join(self.dirname, 'trot-2010-01-08.log'))
        try:
            self.assertEqual(f.readline().rstrip(), '[01:00] Last One')
            self.assertEqual(f.readline().rstrip(), '[02:00] Last One')
        finally:
            f.close()
        f = open(os.path.join(self.dirname, 'trot-2010-01-07.log'))
        try:
            self.assertEqual(f.readline().rstrip(), '[01:00] Third One')
            self.assertEqual(f.readline().rstrip(), '[02:00] Third One')
        finally:
            f.close()

    def test_mail_handler(self):
        handler = make_fake_mail_handler(subject=u'\xf8nicode')
        capture_stderr.start()
        fallback = capture_stderr.current
        try:
            handler.push_application()
            try:
                self.log.warn('This is not mailed')
                try:
                    1 / 0
                except Exception:
                    self.log.exception('This is unfortunate')
            finally:
                handler.pop_application()

            self.assertEqual(len(handler.mails), 1)
            sender, receivers, mail = handler.mails[0]
            self.assertEqual(sender, handler.from_addr)
            self.assert_('=?utf-8?q?=C3=B8nicode?=' in mail)
            self.assert_(re.search('Message type:\s+ERROR', mail))
            self.assert_(re.search('Location:.*test_logbook.py', mail))
            self.assert_(re.search('Module:\s+%s' % __name__, mail))
            self.assert_(re.search('Function:\s+test_mail_handler', mail))
            self.assert_('Message:\r\n\r\nThis is unfortunate' in mail)
            self.assert_('\r\n\r\nTraceback' in mail)
            self.assert_('1 / 0' in mail)
            self.assert_('This is not mailed' in fallback.getvalue())
        finally:
            capture_stderr.end()

    def test_mail_handler_record_limits(self):
        suppression_test = re.compile('This message occurred additional \d+ '
                                      'time\(s\) and was suppressed').search
        handler = make_fake_mail_handler(record_limit=1,
                                         record_delta=timedelta(seconds=0.5))
        handler.push_application()
        try:
            later = datetime.utcnow() + timedelta(seconds=1.1)
            while datetime.utcnow() < later:
                self.log.error('Over and over...')
            # first mail that is always delivered + 0.5 seconds * 2
            # and 0.1 seconds of room for rounding errors makes 3 mails
            self.assertEqual(len(handler.mails), 3)

            # first mail is always delivered
            self.assert_(not suppression_test(handler.mails[0][2]))

            # the next two have a supression count
            self.assert_(suppression_test(handler.mails[1][2]))
            self.assert_(suppression_test(handler.mails[2][2]))
        finally:
            handler.pop_application()

    def test_syslog_handler(self):
        to_test = [
            (socket.AF_INET, ('127.0.0.1', 0)),
        ]
        if hasattr(socket, 'AF_UNIX'):
            to_test.append((socket.AF_UNIX, self.filename))
        for sock_family, address in to_test:
            inc = socket.socket(sock_family, socket.SOCK_DGRAM)
            inc.bind(address)
            inc.settimeout(1)
            for app_name in [None, 'Testing']:
                handler = logbook.SyslogHandler(app_name, inc.getsockname())
                handler.push_application()
                try:
                    self.log.warn('Syslog is weird')
                finally:
                    handler.pop_application()
                try:
                    rv = inc.recvfrom(1024)[0]
                except socket.error:
                    self.fail('got timeout on socket')
                    self.assertEqual(rv, '<12>testlogger: Syslog is weird\x00'
                                     % (app_name and app_name + ':' or ''))

    def test_handler_processors(self):
        handler = make_fake_mail_handler(format_string='''\
Subject: Application Error for {record.extra[path]} [{record.extra[method]}]

Message type:       {record.level_name}
Location:           {record.filename}:{record.lineno}
Module:             {record.module}
Function:           {record.func_name}
Time:               {record.time:%Y-%m-%d %H:%M:%S}
Remote IP:          {record.extra[ip]}
Request:            {record.extra[path]} [{record.extra[method]}]

Message:

{record.message}
''')

        class Request(object):
            remote_addr = '127.0.0.1'
            method = 'GET'
            path = '/index.html'

        def handle_request(request):
            def inject_extra(record):
                record.extra['ip'] = request.remote_addr
                record.extra['method'] = request.method
                record.extra['path'] = request.path

            processor = logbook.Processor(inject_extra)
            processor.push_application()
            try:
                handler.push_application()
                try:
                    try:
                        1 / 0
                    except Exception:
                        self.log.exception('Exception happened during request')
                finally:
                    handler.pop_application()
            finally:
                processor.pop_application()

        handle_request(Request())
        self.assertEqual(len(handler.mails), 1)
        mail = handler.mails[0][2]
        self.assert_('Subject: Application Error '
                     'for /index.html [GET]' in mail)
        self.assert_('1 / 0' in mail)

    def test_custom_handling_test(self):
        class MyTestHandler(logbook.TestHandler):
            def handle(self, record):
                if record.extra.get('flag') != 'testing':
                    return False
                return logbook.TestHandler.handle(self, record)

        class MyLogger(logbook.Logger):
            def process_record(self, record):
                logbook.Logger.process_record(self, record)
                record.extra['flag'] = 'testing'
        log = MyLogger()
        handler = MyTestHandler()
        capture_stderr.start()
        captured = capture_stderr.current
        try:
            handler.push_application()
            try:
                log.warn('From my logger')
                self.log.warn('From another logger')
            finally:
                handler.pop_application()
            self.assert_(handler.has_warning('From my logger'))
            self.assert_('From another logger' in captured.getvalue())
        finally:
            capture_stderr.end()

    def test_custom_handling_tester(self):
        flag = True

        class MyTestHandler(logbook.TestHandler):
            def should_handle(self, record):
                return flag
        null_handler = logbook.NullHandler()
        null_handler.push_application()
        try:
            test_handler = MyTestHandler()
            test_handler.push_application()
            try:
                self.log.warn('1')
                flag = False
                self.log.warn('2')
                self.assert_(test_handler.has_warning('1'))
                self.assert_(not test_handler.has_warning('2'))
            finally:
                test_handler.pop_application()
        finally:
            null_handler.pop_application()

    def test_null_handler(self):
        null_handler = logbook.NullHandler()
        handler = logbook.TestHandler(level='ERROR')
        capture_stderr.start()
        captured = capture_stderr.current
        try:
            null_handler.push_application()
            try:
                handler.push_application()
                try:
                    self.log.error('An error')
                    self.log.warn('A warning')
                finally:
                    handler.pop_application()
            finally:
                null_handler.pop_application()
            self.assertEqual(captured.getvalue(), '')
            self.assertFalse(handler.has_warning('A warning'))
            self.assert_(handler.has_error('An error'))
        finally:
            capture_stderr.end()

    def test_blackhole_setting(self):
        null_handler = logbook.NullHandler()
        heavy_init = logbook.LogRecord.heavy_init
        try:
            def new_heavy_init(self):
                raise RuntimeError('should not be triggered')
            logbook.LogRecord.heavy_init = new_heavy_init
            null_handler.push_application()
            try:
                logbook.warn('Awesome')
            finally:
                null_handler.pop_application()
        finally:
            logbook.LogRecord.heavy_init = heavy_init

        null_handler.bubble = True
        capture_stderr.start()
        captured = capture_stderr.current
        try:
            logbook.warning('Not a blockhole')
            self.assertNotEqual(captured.getvalue(), '')
        finally:
            capture_stderr.end()

    def test_calling_frame(self):
        handler = logbook.TestHandler()
        handler.push_application()
        try:
            logbook.warn('test')
        finally:
            handler.pop_application()
        self.assertEqual(handler.records[0].calling_frame, sys._getframe())

    def test_nested_setups(self):
        capture_stderr.start()
        captured = capture_stderr.current
        try:
            logger = logbook.Logger('App')
            test_handler = logbook.TestHandler(level='WARNING')
            mail_handler = make_fake_mail_handler(bubble=True)

            handlers = logbook.NestedSetup([
                logbook.NullHandler(),
                test_handler,
                mail_handler
            ])

            handlers.push_application()
            try:
                logger.warn('This is a warning')
                logger.error('This is also a mail')
                try:
                    1 / 0
                except Exception:
                    logger.exception()
            finally:
                handlers.pop_application()
            logger.warn('And here we go straight back to stderr')

            self.assert_(test_handler.has_warning('This is a warning'))
            self.assert_(test_handler.has_error('This is also a mail'))
            self.assertEqual(len(mail_handler.mails), 2)
            self.assert_('This is also a mail' in mail_handler.mails[0][2])
            self.assert_('1 / 0' in mail_handler.mails[1][2])
            self.assert_('And here we go straight back to stderr'
                         in captured.getvalue())

            handlers.push_thread()
            try:
                logger.warn('threadbound warning')
            finally:
                handlers.pop_thread()

            handlers.push_thread()
            try:
                logger.warn('applicationbound warning')
            finally:
                handlers.pop_thread()
        finally:
            capture_stderr.end()

    def test_dispatcher(self):
        logger = logbook.Logger('App')
        test_handler = logbook.TestHandler()
        test_handler.push_application()
        try:
            logger.warn('Logbook is too awesome for stdlib')
            self.assertEqual(test_handler.records[0].dispatcher, logger)
        finally:
            test_handler.pop_application()

    def test_filtering(self):
        logger1 = logbook.Logger('Logger1')
        logger2 = logbook.Logger('Logger2')
        handler = logbook.TestHandler()
        outer_handler = logbook.TestHandler()

        def only_1(record, handler):
            return record.dispatcher is logger1
        handler.filter = only_1

        outer_handler.push_application()
        try:
            handler.push_application()
            try:
                logger1.warn('foo')
                logger2.warn('bar')
            finally:
                handler.pop_application()
        finally:
            outer_handler.pop_application()

        self.assert_(handler.has_warning('foo', channel='Logger1'))
        self.assert_(not handler.has_warning('bar', channel='Logger2'))
        self.assert_(not outer_handler.has_warning('foo', channel='Logger1'))
        self.assert_(outer_handler.has_warning('bar', channel='Logger2'))

    def test_different_context_pushing(self):
        h1 = logbook.TestHandler(level=logbook.DEBUG)
        h2 = logbook.TestHandler(level=logbook.INFO)
        h3 = logbook.TestHandler(level=logbook.WARNING)
        logger = logbook.Logger('Testing')

        h1.push_thread()
        try:
            h2.push_application()
            try:
                h3.push_thread()
                try:
                    logger.warn('Wuuu')
                    logger.info('still awesome')
                    logger.debug('puzzled')
                finally:
                    h3.pop_thread()
            finally:
                h2.pop_application()
        finally:
            h1.pop_thread()

        self.assert_(h1.has_debug('puzzled'))
        self.assert_(h2.has_info('still awesome'))
        self.assert_(h3.has_warning('Wuuu'))
        for handler in h1, h2, h3:
            self.assert_(len(handler.records), 1)

    def test_global_functions(self):
        handler = logbook.TestHandler()
        handler.push_application()
        try:
            logbook.debug('a debug message')
            logbook.info('an info message')
            logbook.warn('warning part 1')
            logbook.warning('warning part 2')
            logbook.notice('notice')
            logbook.error('an error')
            logbook.critical('pretty critical')
            logbook.log(logbook.CRITICAL, 'critical too')
        finally:
            handler.pop_application()
        self.assert_(handler.has_debug('a debug message'))
        self.assert_(handler.has_info('an info message'))
        self.assert_(handler.has_warning('warning part 1'))
        self.assert_(handler.has_warning('warning part 2'))
        self.assert_(handler.has_notice('notice'))
        self.assert_(handler.has_error('an error'))
        self.assert_(handler.has_critical('pretty critical'))
        self.assert_(handler.has_critical('critical too'))
        self.assertEqual(handler.records[0].channel, 'Generic')
        self.assertEqual(handler.records[0].dispatcher, None)


class AttributeTestCase(LogbookTestCase):

    def test_level_properties(self):
        self.assertEqual(self.log.level, logbook.NOTSET)
        self.assertEqual(self.log.level_name, 'NOTSET')
        self.log.level_name = 'WARNING'
        self.assertEqual(self.log.level, logbook.WARNING)
        self.log.level = logbook.ERROR
        self.assertEqual(self.log.level_name, 'ERROR')

    def test_reflected_properties(self):
        group = logbook.LoggerGroup()
        group.add_logger(self.log)
        self.assertEqual(self.log.group, group)
        group.level = logbook.ERROR
        self.assertEqual(self.log.level, logbook.ERROR)
        self.assertEqual(self.log.level_name, 'ERROR')
        group.level = logbook.WARNING
        self.assertEqual(self.log.level, logbook.WARNING)
        self.assertEqual(self.log.level_name, 'WARNING')
        self.log.level = logbook.CRITICAL
        group.level = logbook.DEBUG
        self.assertEqual(self.log.level, logbook.CRITICAL)
        self.assertEqual(self.log.level_name, 'CRITICAL')
        group.remove_logger(self.log)
        self.assertEqual(self.log.group, None)


class FlagsTestCase(LogbookTestCase):

    def test_error_flag(self):
        capture_stderr.start()
        captured = capture_stderr.current
        try:
            print_flag = logbook.Flags(errors='print')
            print_flag.push_application()
            try:
                silent_flag = logbook.Flags(errors='silent')
                silent_flag.push_application()
                try:
                    self.log.warn('Foo {42}', 'aha')
                finally:
                    silent_flag.pop_application()
            finally:
                print_flag.pop_application()
            self.assertEqual(captured.getvalue(), '')

            silent_flag = logbook.Flags(errors='silent')
            silent_flag.push_application()
            try:
                print_flag = logbook.Flags(errors='print')
                print_flag.push_application()
                try:
                    self.log.warn('Foo {42}', 'aha')
                finally:
                    print_flag.pop_application()
            finally:
                silent_flag.pop_application()
            self.assertNotEqual(captured.getvalue(), '')

            try:
                raise_flag = logbook.Flags(errors='raise')
                raise_flag.push_application()
                try:
                    self.log.warn('Foo {42}', 'aha')
                finally:
                    raise_flag.pop_application()
            except Exception, e:
                self.assert_('Could not format message with provided '
                             'arguments' in str(e))
            else:
                self.fail('expected exception')
        finally:
            capture_stderr.end()

    def test_disable_introspection(self):
        introspection_flag = logbook.Flags(introspection=False)
        introspection_flag.push_application()
        try:
            h = logbook.TestHandler()
            h.push_application()
            try:
                self.log.warn('Testing')
                self.assert_(h.records[0].frame is None)
                self.assert_(h.records[0].calling_frame is None)
                self.assert_(h.records[0].module is None)
            finally:
                h.pop_application()
        finally:
            introspection_flag.pop_application()


class LoggerGroupTestCase(LogbookTestCase):

    def test_groups(self):
        def inject_extra(record):
            record.extra['foo'] = 'bar'
        group = logbook.LoggerGroup(processor=inject_extra)
        group.level = logbook.ERROR
        group.add_logger(self.log)
        handler = logbook.TestHandler()
        handler.push_application()
        try:
            self.log.warn('A warning')
            self.log.error('An error')
            self.assert_(not handler.has_warning('A warning'))
            self.assert_(handler.has_error('An error'))
            self.assertEqual(handler.records[0].extra['foo'], 'bar')
        finally:
            handler.pop_application()


class DefaultConfigurationTestCase(LogbookTestCase):

    def test_default_handlers(self):
        capture_stderr.start()
        stream = capture_stderr.current
        try:
            self.log.warn('Aha!')
            captured = stream.getvalue()
            self.assert_('WARNING: testlogger: Aha!' in captured)
        finally:
            capture_stderr.end()


class LoggingCompatTestCase(LogbookTestCase):

    def test_basic_compat(self):
        from logging import getLogger
        from logbook.compat import redirected_logging

        name = 'test_logbook-%d' % randrange(1 << 32)
        logger = getLogger(name)
        capture_stderr.start()
        captured = capture_stderr.current
        try:
            redirector = redirected_logging()
            redirector.start()
            try:
                logger.debug('This is from the old system')
                logger.info('This is from the old system')
                logger.warn('This is from the old system')
                logger.error('This is from the old system')
                logger.critical('This is from the old system')
            finally:
                redirector.end()
            self.assert_(('WARNING: %s: This is from the old system' % name)
                         in captured.getvalue())
        finally:
            capture_stderr.end()

    def test_redirect_logbook(self):
        import logging
        from logbook.compat import LoggingHandler
        out = StringIO()
        logger = logging.getLogger()
        old_handlers = logger.handlers[:]
        handler = logging.StreamHandler(out)
        handler.setFormatter(logging.Formatter(
            '%(name)s:%(levelname)s:%(message)s'))
        logger.handlers[:] = [handler]
        try:
            logging_handler = LoggingHandler()
            logging_handler.push_application()
            try:
                self.log.warn("This goes to logging")
                pieces = out.getvalue().strip().split(':')
                self.assertEqual(pieces, [
                    'testlogger',
                    'WARNING',
                    'This goes to logging'
                ])
            finally:
                logging_handler.pop_application()
        finally:
            logger.handlers[:] = old_handlers


class WarningsCompatTestCase(LogbookTestCase):

    def test_warning_redirections(self):
        from logbook.compat import redirected_warnings
        handler = logbook.TestHandler()
        handler.push_application()
        try:
            redirector = redirected_warnings()
            redirector.start()
            try:
                from warnings import warn
                warn(RuntimeWarning('Testing'))
            finally:
                redirector.end()
        finally:
            handler.pop_application()
        self.assertEqual(len(handler.records), 1)
        self.assertEqual('[WARNING] RuntimeWarning: Testing',
                         handler.formatted_records[0])
        self.assert_('test_logbook.py' in handler.records[0].filename)


class MoreTestCase(LogbookTestCase):

    def test_fingerscrossed(self):
        from logbook.more import FingersCrossedHandler
        handler = FingersCrossedHandler(logbook.default_handler,
                                        logbook.WARNING)

        # if no warning occurs, the infos are not logged
        handler.push_application()
        try:
            capture_stderr.start()
            captured = capture_stderr.current
            try:
                self.log.info('some info')
            finally:
                capture_stderr.end()
            self.assertEqual(captured.getvalue(), '')
            self.assert_(not handler.triggered)
        finally:
            handler.pop_application()

        # but if it does, all log messages are output
        handler.push_application()
        try:
            capture_stderr.start()
            captured = capture_stderr.current
            try:
                self.log.info('some info')
                self.log.warning('something happened')
                self.log.info('something else happened')
            finally:
                capture_stderr.end()
            logs = captured.getvalue()
            self.assert_('some info' in logs)
            self.assert_('something happened' in logs)
            self.assert_('something else happened' in logs)
            self.assert_(handler.triggered)
        finally:
            handler.pop_application()

    def test_fingerscrossed_factory(self):
        from logbook.more import FingersCrossedHandler

        handlers = []

        def handler_factory(record, fch):
            handler = logbook.TestHandler()
            handlers.append(handler)
            return handler

        def make_fch():
            return FingersCrossedHandler(handler_factory, logbook.WARNING,
                                         )

        fch = make_fch()
        fch.push_application()
        try:
            self.log.info('some info')
            self.assertEqual(len(handlers), 0)
            self.log.warning('a warning')
            self.assertEqual(len(handlers), 1)
            self.log.error('an error')
            self.assertEqual(len(handlers), 1)
            self.assert_(handlers[0].has_infos)
            self.assert_(handlers[0].has_warnings)
            self.assert_(handlers[0].has_errors)
            self.assert_(not handlers[0].has_notices)
            self.assert_(not handlers[0].has_criticals)
            self.assert_(not handlers[0].has_debugs)
        finally:
            fch.pop_application()

        fch = make_fch()
        fch.push_application()
        try:
            self.log.info('some info')
            self.log.warning('a warning')
            self.assertEqual(len(handlers), 2)
        finally:
            fch.pop_application()

    def test_fingerscrossed_buffer_size(self):
        from logbook.more import FingersCrossedHandler
        logger = logbook.Logger('Test')
        test_handler = logbook.TestHandler()
        handler = FingersCrossedHandler(test_handler, buffer_size=3)

        handler.push_application()
        try:
            logger.info('Never gonna give you up')
            logger.warn('Aha!')
            logger.warn('Moar!')
            logger.error('Pure hate!')
        finally:
            handler.pop_application()

        self.assertEqual(test_handler.formatted_records, [
            '[WARNING] Test: Aha!',
            '[WARNING] Test: Moar!',
            '[ERROR] Test: Pure hate!'
        ])

    def test_tagged(self):
        from logbook.more import TaggingLogger, TaggingHandler
        stream = StringIO()
        second_handler = logbook.StreamHandler(stream)

        logger = TaggingLogger('name', ['cmd'])
        handler = TaggingHandler(dict(
            info=logbook.default_handler,
            cmd=second_handler,
            both=[logbook.default_handler, second_handler],
        ))
        handler.bubble = False

        handler.push_application()
        try:
            capture_stderr.start()
            captured = capture_stderr.current
            try:
                logger.log('info', 'info message')
                logger.log('both', 'all message')
                logger.cmd('cmd message')
            finally:
                capture_stderr.end()
        finally:
            handler.pop_application()

        stderr = captured.getvalue()

        self.assert_('info message' in stderr)
        self.assert_('all message' in stderr)
        self.assert_('cmd message' not in stderr)

        stringio = stream.getvalue()

        self.assert_('info message' not in stringio)
        self.assert_('all message' in stringio)
        self.assert_('cmd message' in stringio)

    @require('jinja2')
    def test_jinja_formatter(self):
        from logbook.more import JinjaFormatter
        fmter = JinjaFormatter('{{ record.channel }}/{{ record.level_name }}')
        handler = logbook.TestHandler()
        handler.formatter = fmter
        handler.push_application()
        try:
            self.log.info('info')
        finally:
            handler.pop_application()
        self.assert_('testlogger/INFO' in handler.formatted_records)

    @missing('jinja2')
    def test_missing_jinja2(self):
        from logbook.more import JinjaFormatter
        # check the RuntimeError is raised
        self.assertRaises(RuntimeError, JinjaFormatter, 'dummy')


class QueuesTestCase(LogbookTestCase):

    @require('zmq')
    def test_zeromq_handler(self):
        from logbook.queues import ZeroMQHandler, ZeroMQSubscriber
        tests = [
            u'Logging something',
            u'Something with umlauts äöü',
            u'Something else for good measure',
        ]
        uri = 'tcp://127.0.0.1:42000'
        handler = ZeroMQHandler(uri)
        subscriber = ZeroMQSubscriber(uri)
        for test in tests:
            handler.push_application()
            try:
                self.log.warn(test)
                record = subscriber.recv()
                self.assertEqual(record.message, test)
                self.assertEqual(record.channel, self.log.name)
            finally:
                handler.pop_application()

    @require('zmq')
    def test_zeromq_background_thread(self):
        from logbook.queues import ZeroMQHandler, ZeroMQSubscriber
        uri = 'tcp://127.0.0.1:42001'
        handler = ZeroMQHandler(uri)
        subscriber = ZeroMQSubscriber(uri)
        test_handler = logbook.TestHandler()
        controller = subscriber.dispatch_in_background(test_handler)

        handler.push_application()
        try:
            self.log.warn('This is a warning')
            self.log.error('This is an error')
        finally:
            handler.pop_application()

        # stop the controller.  This will also stop the loop and join the
        # background process.  Before that we give it a fraction of a second
        # to get all results
        time.sleep(0.1)
        controller.stop()

        self.assert_(test_handler.has_warning('This is a warning'))
        self.assert_(test_handler.has_error('This is an error'))

    @missing('zmq')
    def test_missing_zeromq(self):
        from logbook.queues import ZeroMQHandler, ZeroMQSubscriber
        self.assertRaises(RuntimeError, ZeroMQHandler,
                          'tcp://127.0.0.1:42000')
        self.assertRaises(RuntimeError, ZeroMQSubscriber,
                          'tcp://127.0.0.1:42000')

    @require('multiprocessing')
    def test_multi_processing_handler(self):
        from multiprocessing import Process, Queue
        from logbook.queues import MultiProcessingHandler, \
             MultiProcessingSubscriber
        queue = Queue(-1)
        test_handler = logbook.TestHandler()
        subscriber = MultiProcessingSubscriber(queue)

        def send_back():
            handler = MultiProcessingHandler(queue)
            handler.push_application()
            try:
                logbook.warn('Hello World')
            finally:
                handler.pop_application()

        p = Process(target=send_back)
        p.start()
        p.join()

        test_handler.push_application()
        try:
            subscriber.dispatch_once()
            self.assert_(test_handler.has_warning('Hello World'))
        finally:
            test_handler.pop_application()

    def test_threaded_wrapper_handler(self):
        from logbook.queues import ThreadedWrapperHandler
        test_handler = logbook.TestHandler()
        handler = ThreadedWrapperHandler(test_handler)
        handler.push_application()
        try:
            self.log.warn('Just testing')
            self.log.error('More testing')
        finally:
            handler.pop_application()

        # give it some time to sync up
        handler.close()

        self.assert_(not handler.controller.running)
        self.assert_(test_handler.has_warning('Just testing'))
        self.assert_(test_handler.has_error('More testing'))

    @require('execnet')
    def test_execnet_handler(self):
        def run_on_remote(channel):
            import logbook
            from logbook.queues import ExecnetChannelHandler
            handler = ExecnetChannelHandler(channel)
            log = logbook.Logger("Execnet")
            handler.push_application()
            log.info('Execnet works')

        import execnet
        gw = execnet.makegateway()
        channel = gw.remote_exec(run_on_remote)
        from logbook.queues import ExecnetChannelSubscriber
        subscriber = ExecnetChannelSubscriber(channel)
        record = subscriber.recv()
        self.assertEqual(record.msg, 'Execnet works')
        gw.exit()

    @require('multiprocessing')
    def test_subscriber_group(self):
        from multiprocessing import Process, Queue
        from logbook.queues import MultiProcessingHandler, \
                                   MultiProcessingSubscriber, SubscriberGroup
        a_queue = Queue(-1)
        b_queue = Queue(-1)
        test_handler = logbook.TestHandler()
        subscriber = SubscriberGroup([
            MultiProcessingSubscriber(a_queue),
            MultiProcessingSubscriber(b_queue)
        ])

        def make_send_back(message, queue):
            def send_back():
                handler = MultiProcessingHandler(queue)
                handler.push_application()
                try:
                    logbook.warn(message)
                finally:
                    handler.pop_application()
            return send_back

        for _ in range(10):
            p1 = Process(target=make_send_back('foo', a_queue))
            p2 = Process(target=make_send_back('bar', b_queue))
            p1.start()
            p2.start()
            p1.join()
            p2.join()
            messages = [subscriber.recv().message for i in 1, 2]
            self.assertEqual(sorted(messages), ['bar', 'foo'])


class TicketingTestCase(LogbookTestCase):

    @require('sqlalchemy')
    def test_basic_ticketing(self):
        from logbook.ticketing import TicketingHandler
        handler = TicketingHandler('sqlite:///')
        handler.push_application()
        try:
            for x in xrange(5):
                self.log.warn('A warning')
                self.log.info('An error')
                if x < 2:
                    try:
                        1 / 0
                    except Exception:
                        self.log.exception()
        finally:
            handler.pop_application()

        self.assertEqual(handler.db.count_tickets(), 3)
        tickets = handler.db.get_tickets()
        self.assertEqual(len(tickets), 3)
        self.assertEqual(tickets[0].level, logbook.INFO)
        self.assertEqual(tickets[1].level, logbook.WARNING)
        self.assertEqual(tickets[2].level, logbook.ERROR)
        self.assertEqual(tickets[0].occurrence_count, 5)
        self.assertEqual(tickets[1].occurrence_count, 5)
        self.assertEqual(tickets[2].occurrence_count, 2)
        self.assertEqual(tickets[0].last_occurrence.level, logbook.INFO)

        tickets[0].solve()
        self.assert_(tickets[0].solved)
        tickets[0].delete()

        ticket = handler.db.get_ticket(tickets[1].ticket_id)
        self.assertEqual(ticket, tickets[1])

        occurrences = handler.db.get_occurrences(tickets[2].ticket_id,
                                                 order_by='time')
        self.assertEqual(len(occurrences), 2)
        record = occurrences[0]
        self.assert_('test_logbook.py' in record.filename)
        self.assertEqual(record.func_name, 'test_basic_ticketing')
        self.assertEqual(record.level, logbook.ERROR)
        self.assertEqual(record.thread, thread.get_ident())
        self.assertEqual(record.process, os.getpid())
        self.assertEqual(record.channel, 'testlogger')
        self.assert_('1 / 0' in record.formatted_exception)


class HelperTestCase(unittest.TestCase):

    def test_jsonhelper(self):
        from logbook.helpers import to_safe_json

        class Bogus(object):
            def __str__(self):
                return 'bogus'

        rv = to_safe_json([
            None,
            'foo',
            u'jäger',
            1,
            datetime(2000, 1, 1),
            {'jäger1': 1, u'jäger2': 2, Bogus(): 3, 'invalid': object()},
            object()  # invalid
        ])
        self.assertEqual(
            rv, [None, u'foo', u'jäger', 1, '2000-01-01T00:00:00Z',
                 {u'jäger1': 1, u'jäger2': 2, u'bogus': 3,
                  'invalid': None}, None])

    def test_datehelpers(self):
        from logbook.helpers import format_iso8601, parse_iso8601
        now = datetime.now()
        rv = format_iso8601()
        self.assertEqual(rv[:4], str(now.year))

        self.assertRaises(ValueError, parse_iso8601, 'foo')
        v = parse_iso8601('2000-01-01T00:00:00.12Z')
        self.assertEqual(v.microsecond, 120000)
        v = parse_iso8601('2000-01-01T12:00:00+01:00')
        self.assertEqual(v.hour, 11)
        v = parse_iso8601('2000-01-01T12:00:00-01:00')
        self.assertEqual(v.hour, 13)


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        for modname in _skipped_modules:
            msg = '*** Failed to import %s, tests skipped.\n' % modname
            sys.stderr.write(msg)
