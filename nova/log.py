# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Nova logging handler.

This module adds to logging functionality by adding the option to specify
a context object when calling the various log methods.  If the context object
is not specified, default formatting is used.

It also allows setting of formatting information through flags.
"""


import cStringIO
import inspect
import json
import logging
import logging.handlers
import os
import sys
import traceback

from nova import flags
from nova import version


FLAGS = flags.FLAGS

flags.DEFINE_string('logging_context_format_string',
                    '%(asctime)s %(levelname)s %(name)s '
                    '[%(request_id)s %(user)s '
                    '%(project)s] %(message)s',
                    'format string to use for log messages with context')

flags.DEFINE_string('logging_default_format_string',
                    '%(asctime)s %(levelname)s %(name)s [-] '
                    '%(message)s',
                    'format string to use for log messages without context')

flags.DEFINE_string('logging_debug_format_suffix',
                    'from %(processName)s (pid=%(process)d) %(funcName)s'
                    ' %(pathname)s:%(lineno)d',
                    'data to append to log format when level is DEBUG')

flags.DEFINE_string('logging_exception_prefix',
                    '(%(name)s): TRACE: ',
                    'prefix each line of exception output with this format')

flags.DEFINE_list('default_log_levels',
                  ['amqplib=WARN',
                   'sqlalchemy=WARN',
                   'eventlet.wsgi.server=WARN'],
                  'list of logger=LEVEL pairs')

flags.DEFINE_bool('use_syslog', False, 'output to syslog')
flags.DEFINE_string('logfile', None, 'output to named file')


# A list of things we want to replicate from logging.
# levels
CRITICAL = logging.CRITICAL
FATAL = logging.FATAL
ERROR = logging.ERROR
WARNING = logging.WARNING
WARN = logging.WARN
INFO = logging.INFO
DEBUG = logging.DEBUG
NOTSET = logging.NOTSET
# methods
getLogger = logging.getLogger
debug = logging.debug
info = logging.info
warning = logging.warning
warn = logging.warn
error = logging.error
exception = logging.exception
critical = logging.critical
log = logging.log
# handlers
StreamHandler = logging.StreamHandler
WatchedFileHandler = logging.handlers.WatchedFileHandler
# logging.SysLogHandler is nicer than logging.logging.handler.SysLogHandler.
SysLogHandler = logging.handlers.SysLogHandler


# our new audit level
AUDIT = logging.INFO + 1
logging.addLevelName(AUDIT, 'AUDIT')


def _dictify_context(context):
    if context == None:
        return None
    if not isinstance(context, dict) \
    and getattr(context, 'to_dict', None):
        context = context.to_dict()
    return context


def _get_binary_name():
    return os.path.basename(inspect.stack()[-1][1])


def get_log_file_path(binary=None):
    if FLAGS.logfile:
        return FLAGS.logfile
    if FLAGS.logdir:
        binary = binary or _get_binary_name()
        return '%s.log' % (os.path.join(FLAGS.logdir, binary),)


def basicConfig():
    logging.basicConfig()
    for handler in logging.root.handlers:
        handler.setFormatter(_formatter)
    if FLAGS.verbose:
        logging.root.setLevel(logging.DEBUG)
    else:
        logging.root.setLevel(logging.INFO)
    if FLAGS.use_syslog:
        syslog = SysLogHandler(address='/dev/log')
        syslog.setFormatter(_formatter)
        logging.root.addHandler(syslog)
    logpath = get_log_file_path()
    if logpath:
        logfile = WatchedFileHandler(logpath)
        logfile.setFormatter(_formatter)
        logging.root.addHandler(logfile)


class NovaLogger(logging.Logger):
    """
    NovaLogger manages request context and formatting.

    This becomes the class that is instanciated by logging.getLogger.
    """
    def __init__(self, name, level=NOTSET):
        level_name = self._get_level_from_flags(name, FLAGS)
        level = globals()[level_name]
        logging.Logger.__init__(self, name, level)

    def _get_level_from_flags(self, name, FLAGS):
        # if exactly "nova", or a child logger, honor the verbose flag
        if (name == "nova" or name.startswith("nova.")) and FLAGS.verbose:
            return 'DEBUG'
        for pair in FLAGS.default_log_levels:
            logger, _sep, level = pair.partition('=')
            # NOTE(todd): if we set a.b, we want a.b.c to have the same level
            #             (but not a.bc, so we check the dot)
            if name == logger:
                return level
            if name.startswith(logger) and name[len(logger)] == '.':
                return level
        return 'INFO'

    def _log(self, level, msg, args, exc_info=None, extra=None, context=None):
        """Extract context from any log call"""
        if not extra:
            extra = {}
        if context:
            extra.update(_dictify_context(context))
        extra.update({"nova_version": version.version_string_with_vcs()})
        logging.Logger._log(self, level, msg, args, exc_info, extra)

    def addHandler(self, handler):
        """Each handler gets our custom formatter"""
        handler.setFormatter(_formatter)
        logging.Logger.addHandler(self, handler)

    def audit(self, msg, *args, **kwargs):
        """Shortcut for our AUDIT level"""
        if self.isEnabledFor(AUDIT):
            self._log(AUDIT, msg, args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        """Logging.exception doesn't handle kwargs, so breaks context"""
        if not kwargs.get('exc_info'):
            kwargs['exc_info'] = 1
        self.error(msg, *args, **kwargs)
        # NOTE(todd): does this really go here, or in _log ?
        extra = kwargs.get('extra')
        if not extra:
            return
        env = extra.get('environment')
        if env:
            env = env.copy()
            for k in env.keys():
                if not isinstance(env[k], str):
                    env.pop(k)
            message = "Environment: %s" % json.dumps(env)
            kwargs.pop('exc_info')
            self.error(message, **kwargs)


def handle_exception(type, value, tb):
    logging.root.critical(str(value), exc_info=(type, value, tb))


sys.excepthook = handle_exception
logging.setLoggerClass(NovaLogger)


class NovaRootLogger(NovaLogger):
    pass

if not isinstance(logging.root, NovaRootLogger):
    logging.root = NovaRootLogger("nova.root", WARNING)
    NovaLogger.root = logging.root
    NovaLogger.manager.root = logging.root


class NovaFormatter(logging.Formatter):
    """
    A nova.context.RequestContext aware formatter configured through flags.

    The flags used to set format strings are: logging_context_foramt_string
    and logging_default_format_string.  You can also specify
    logging_debug_format_suffix to append extra formatting if the log level is
    debug.

    For information about what variables are available for the formatter see:
    http://docs.python.org/library/logging.html#formatter
    """

    def format(self, record):
        """Uses contextstring if request_id is set, otherwise default"""
        if record.__dict__.get('request_id', None):
            self._fmt = FLAGS.logging_context_format_string
        else:
            self._fmt = FLAGS.logging_default_format_string
        if record.levelno == logging.DEBUG \
        and FLAGS.logging_debug_format_suffix:
            self._fmt += " " + FLAGS.logging_debug_format_suffix
        # Cache this on the record, Logger will respect our formated copy
        if record.exc_info:
            record.exc_text = self.formatException(record.exc_info, record)
        return logging.Formatter.format(self, record)

    def formatException(self, exc_info, record=None):
        """Format exception output with FLAGS.logging_exception_prefix"""
        if not record:
            return logging.Formatter.formatException(self, exc_info)
        stringbuffer = cStringIO.StringIO()
        traceback.print_exception(exc_info[0], exc_info[1], exc_info[2],
                                  None, stringbuffer)
        lines = stringbuffer.getvalue().split("\n")
        stringbuffer.close()
        formatted_lines = []
        for line in lines:
            pl = FLAGS.logging_exception_prefix % record.__dict__
            fl = "%s%s" % (pl, line)
            formatted_lines.append(fl)
        return "\n".join(formatted_lines)

_formatter = NovaFormatter()


def audit(msg, *args, **kwargs):
    """Shortcut for logging to root log with sevrity 'AUDIT'."""
    if len(logging.root.handlers) == 0:
        basicConfig()
    logging.root.log(AUDIT, msg, *args, **kwargs)
