# -*- coding: utf-8 -*-

import Queue
import socket

import logging
_logger = logging.getLogger("pelita.mailbox")
_logger.setLevel(logging.DEBUG)

from pelita.messaging.utils import SuspendableThread, CloseThread
from pelita.messaging.remote import MessageSocketConnection
from pelita.messaging import Actor, StopProcessing, DeadConnection, ForwardingActor, Query, Request, RequestDB

class JsonThreadedInbox(SuspendableThread):
    def __init__(self, mailbox, **kwargs):
        self.mailbox = mailbox
        self.connection = mailbox.connection

        super(JsonThreadedInbox, self).__init__(**kwargs)

    def _run(self):
        try:
            recv = self.connection.read()
        except socket.timeout as e:
            _logger.debug("socket.timeout: %r (%r)" % (e, self))
            return
        except DeadConnection:
            _logger.debug("Remote connection is dead, closing mailbox in %r", self)
            self.mailbox.stop()
            raise CloseThread

        message = recv
        _logger.info("Processing inbox %r", message.dict)
        # add the mailbox to the message
        message.mailbox = self.mailbox
        self.forward_message(message)

    def forward_message(self, message):
        self.mailbox.put(message)

# TODO Not in use now, we rely on timeout until we know better
#    def stop(self):
#        SuspendableThread.stop(self)
#
#        self.connection.connection.shutdown(socket.SHUT_RDWR)
#        self.connection.close()

class IncomingActor(Actor):
    pass

class ForwardingInbox(ForwardingActor, JsonThreadedInbox):
    pass

class JsonThreadedOutbox(SuspendableThread):
    def __init__(self, connection):
        super(JsonThreadedOutbox, self).__init__()

        self.connection = connection
        self._queue = Queue.Queue()

    def _run(self):
        self.handle_outbox()

    def handle_outbox(self):
        try:
            to_send = self._queue.get(True, 3)

            _logger.info("Processing outbox %r", to_send)
            if to_send is StopProcessing:
                raise CloseThread

            self.connection.send(to_send)
        except Queue.Empty:
            pass

class MailboxConnection(Actor):
    """A mailbox bundles an incoming and an outgoing connection."""
    def __init__(self, connection, main_actor, **kwargs):
        super(MailboxConnection, self).__init__(**kwargs)
        self.connection = MessageSocketConnection(connection)

#        self.inbox = ForwardingInbox(self, request_db=self._requests)
#        self.inbox.forward_to = main_actor

        self.main_actor = main_actor

        self.inbox = JsonThreadedInbox(self)
        self.outbox = JsonThreadedOutbox(self.connection)

#        main_actor.link(self.inbox)

    def on_receive(self, message):
        print "Forwarding"
        self.main_actor.forward(message)

    def on_start(self):
        _logger.info("Starting mailbox %r", self)
        self.inbox.start()
        self.outbox.start()

    def on_stop(self):
        _logger.info("Stopping mailbox %r", self)
        #self.inbox._queue.put(StopProcessing)
        self.outbox._queue.put(StopProcessing) # I need to to this or the thread will not stop...
        self.inbox.stop()
        self.outbox.stop()
        self.connection.close()

    def put_remote(self, message, block=True, timeout=None):
        self.outbox._queue.put(message, block, timeout)

    def put_query_remote(self, message):
        print "query", message
        """Put a query into the outbox and return the Request object."""
        if isinstance(message, Query):
            # Update the message.id
            message.id = self.request_db.create_id(message.id)

            req_obj = Request(message.id)
            # save the id to the _requests dict
            self.request_db.add_request(req_obj)

            message.mailbox = self

            self.put_remote(message)
            return req_obj
        else:
            raise ValueError