# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2010 Openstack, LLC.
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
Scheduler Service
"""

import functools

from nova import db
from nova import flags
from nova import log as logging
from nova import manager
from nova import rpc
from nova import utils

LOG = logging.getLogger('nova.scheduler.manager')
FLAGS = flags.FLAGS
flags.DEFINE_string('scheduler_driver',
                    'nova.scheduler.chance.ChanceScheduler',
                    'Driver to use for the scheduler')


class SchedulerManager(manager.Manager):
    """Chooses a host to run instances on."""
    def __init__(self, scheduler_driver=None, *args, **kwargs):
        if not scheduler_driver:
            scheduler_driver = FLAGS.scheduler_driver
        self.driver = utils.import_object(scheduler_driver)
        super(SchedulerManager, self).__init__(*args, **kwargs)

    def __getattr__(self, key):
        """Converts all method calls to use the schedule method"""
        return functools.partial(self._schedule, key)

    def _schedule(self, method, context, topic, *args, **kwargs):
        """Tries to call schedule_* method on the driver to retrieve host.

        Falls back to schedule(context, topic) if method doesn't exist.
        """
        driver_method = 'schedule_%s' % method
        elevated = context.elevated()
        try:
            host = getattr(self.driver, driver_method)(elevated, *args,
                                                       **kwargs)
        except AttributeError:
            host = self.driver.schedule(elevated, topic, *args, **kwargs)

        rpc.cast(context,
                 db.queue_get_for(context, topic, host),
                 {"method": method,
                  "args": kwargs})
        LOG.debug(_("Casting to %(topic)s %(host)s for %(method)s") % locals())
