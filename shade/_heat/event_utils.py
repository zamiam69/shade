# Copyright 2015 Red Hat Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import collections
import time


def get_events(cloud, stack_id, event_args, marker=None, limit=None):
    # TODO(mordred) FIX THIS ONCE assert_calls CAN HANDLE QUERY STRINGS
    params = collections.OrderedDict()
    for k in sorted(event_args.keys()):
        params[k] = event_args[k]

    if marker:
        event_args['marker'] = marker
    if limit:
        event_args['limit'] = limit

    events = cloud._orchestration_client.get(
        '/stacks/{id}/events'.format(id=stack_id),
        params=params)

    # Show which stack the event comes from (for nested events)
    for e in events:
        e['stack_name'] = stack_id.split("/")[0]
    return events


def poll_for_events(
        cloud, stack_name, action=None, poll_period=5, marker=None):
    """Continuously poll events and logs for performed action on stack."""

    if action:
        stop_status = ('%s_FAILED' % action, '%s_COMPLETE' % action)
        stop_check = lambda a: a in stop_status
    else:
        stop_check = lambda a: a.endswith('_COMPLETE') or a.endswith('_FAILED')

    no_event_polls = 0
    msg_template = "\n Stack %(name)s %(status)s \n"

    def is_stack_event(event):
        if event.get('resource_name', '') != stack_name:
            return False

        phys_id = event.get('physical_resource_id', '')
        links = dict((l.get('rel'),
                      l.get('href')) for l in event.get('links', []))
        stack_id = links.get('stack', phys_id).rsplit('/', 1)[-1]
        return stack_id == phys_id

    while True:
        events = get_events(
            cloud, stack_id=stack_name,
            event_args={'sort_dir': 'asc', 'marker': marker})

        if len(events) == 0:
            no_event_polls += 1
        else:
            no_event_polls = 0
            # set marker to last event that was received.
            marker = getattr(events[-1], 'id', None)

            for event in events:
                # check if stack event was also received
                if is_stack_event(event):
                    stack_status = getattr(event, 'resource_status', '')
                    msg = msg_template % dict(
                        name=stack_name, status=stack_status)
                    if stop_check(stack_status):
                        return stack_status, msg

        if no_event_polls >= 2:
            # after 2 polls with no events, fall back to a stack get
            stack = cloud.get_stack(stack_name)
            stack_status = stack['stack_status']
            msg = msg_template % dict(
                name=stack_name, status=stack_status)
            if stop_check(stack_status):
                return stack_status, msg
            # go back to event polling again
            no_event_polls = 0

        time.sleep(poll_period)
