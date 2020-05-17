from graphql import graphql
from graphql.execution.executors.sync import SyncExecutor
from rx import Observable, Observer

from .base import BaseSubscriptionServer
from .constants import GQL_CONNECTION_ACK, GQL_CONNECTION_ERROR


class BaseSyncSubscriptionServer(BaseSubscriptionServer):
    graphql_executor = SyncExecutor

    def unsubscribe(self, connection_context, op_id):
        if connection_context.has_operation(op_id):
            # Close async iterator
            connection_context.get_operation(op_id).dispose()
            # Close operation
            connection_context.remove_operation(op_id)
        self.on_operation_complete(connection_context, op_id)

    def on_operation_complete(self, connection_context, op_id):
        pass

    def execute(self, request_context, params):
        return graphql(self.schema, **dict(params, allow_subscriptions=True))

    def handle(self, ws, request_context=None):
        raise NotImplementedError("handle method not implemented")

    def on_open(self, connection_context):
        pass

    def on_connect(self, connection_context, payload):
        pass

    def on_close(self, connection_context):
        remove_operations = list(connection_context.operations.keys())
        for op_id in remove_operations:
            self.unsubscribe(connection_context, op_id)

    def on_connection_init(self, connection_context, op_id, payload):
        try:
            self.on_connect(connection_context, payload)
            self.send_message(connection_context, op_type=GQL_CONNECTION_ACK)

        except Exception as e:
            self.send_error(connection_context, op_id, e, GQL_CONNECTION_ERROR)
            connection_context.close(1011)

    def on_stop(self, connection_context, op_id):
        self.unsubscribe(connection_context, op_id)

    def on_start(self, connection_context, op_id, params):
        try:
            execution_result = self.execute(connection_context.request_context, params)
            assert isinstance(
                execution_result, Observable
            ), "A subscription must return an observable"
            execution_result.subscribe(
                SubscriptionObserver(
                    connection_context,
                    op_id,
                    self.send_execution_result,
                    self.send_error,
                    self.on_close,
                )
            )
        except Exception as e:
            self.send_error(connection_context, op_id, str(e))


class SubscriptionObserver(Observer):
    def __init__(
        self, connection_context, op_id, send_execution_result, send_error, on_close
    ):
        self.connection_context = connection_context
        self.op_id = op_id
        self.send_execution_result = send_execution_result
        self.send_error = send_error
        self.on_close = on_close

    def on_next(self, value):
        self.send_execution_result(self.connection_context, self.op_id, value)

    def on_completed(self):
        self.on_close(self.connection_context)

    def on_error(self, error):
        self.send_error(self.connection_context, self.op_id, error)
