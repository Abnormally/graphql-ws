import json
from collections import OrderedDict

from graphql import format_error

from .constants import (
    GQL_CONNECTION_ERROR,
    GQL_CONNECTION_INIT,
    GQL_CONNECTION_TERMINATE,
    GQL_DATA,
    GQL_ERROR,
    GQL_START,
    GQL_STOP,
)


class ConnectionClosedException(Exception):
    pass


class BaseConnectionContext(object):
    def __init__(self, ws, request_context=None):
        self.ws = ws
        self.operations = {}
        self.request_context = request_context

    def has_operation(self, op_id):
        return op_id in self.operations

    def register_operation(self, op_id, async_iterator):
        self.operations[op_id] = async_iterator

    def get_operation(self, op_id):
        return self.operations[op_id]

    def remove_operation(self, op_id):
        del self.operations[op_id]

    def receive(self):
        raise NotImplementedError("receive method not implemented")

    def send(self, data):
        raise NotImplementedError("send method not implemented")

    @property
    def closed(self):
        raise NotImplementedError("closed property not implemented")

    def close(self, code):
        raise NotImplementedError("close method not implemented")


class BaseSubscriptionServer(object):
    graphql_executor = None

    def __init__(self, schema, keep_alive=True):
        self.schema = schema
        self.keep_alive = keep_alive

    def process_message(self, connection_context, parsed_message):
        op_id = parsed_message.get("id")
        op_type = parsed_message.get("type")
        payload = parsed_message.get("payload")

        if op_type == GQL_CONNECTION_INIT:
            return self.on_connection_init(connection_context, op_id, payload)

        elif op_type == GQL_CONNECTION_TERMINATE:
            return self.on_connection_terminate(connection_context, op_id)

        elif op_type == GQL_START:
            assert isinstance(payload, dict), "The payload must be a dict"

            params = self.get_graphql_params(connection_context, payload)
            if not isinstance(params, dict):
                error = Exception(
                    "Invalid params returned from get_graphql_params!"
                    " Return values must be a dict."
                )
                return self.send_error(connection_context, op_id, error)

            # If we already have a subscription with this id, unsubscribe from
            # it first
            if connection_context.has_operation(op_id):
                self.unsubscribe(connection_context, op_id)

            params = self.get_graphql_params(connection_context, payload)
            return self.on_start(connection_context, op_id, params)

        elif op_type == GQL_STOP:
            return self.on_stop(connection_context, op_id)

        else:
            return self.send_error(
                connection_context,
                op_id,
                Exception("Invalid message type: {}.".format(op_type)),
            )

    def on_connection_init(self, connection_context, op_id, payload):
        raise NotImplementedError("on_connection_init method not implemented")

    def on_connection_terminate(self, connection_context, op_id):
        return connection_context.close(1011)

    def get_graphql_params(self, connection_context, payload):
        return {
            "request_string": payload.get("query"),
            "variable_values": payload.get("variables"),
            "operation_name": payload.get("operationName"),
            "context_value": payload.get("context"),
            "executor": self.graphql_executor(),
        }

    def on_open(self, connection_context):
        raise NotImplementedError("on_open method not implemented")

    def on_stop(self, connection_context, op_id):
        raise NotImplementedError("on_stop method not implemented")

    def send_message(self, connection_context, op_id=None, op_type=None, payload=None):
        message = self.build_message(op_id, op_type, payload)
        assert message, "You need to send at least one thing"
        return connection_context.send(message)

    def build_message(self, id, op_type, payload):
        message = {}
        if id is not None:
            message["id"] = id
        if op_type is not None:
            message["type"] = op_type
        if payload is not None:
            message["payload"] = payload
        return message

    def send_execution_result(self, connection_context, op_id, execution_result):
        result = self.execution_result_to_dict(execution_result)
        return self.send_message(connection_context, op_id, GQL_DATA, result)

    def execution_result_to_dict(self, execution_result):
        result = OrderedDict()
        if execution_result.data:
            result["data"] = execution_result.data
        if execution_result.errors:
            result["errors"] = [
                format_error(error) for error in execution_result.errors
            ]
        return result

    def send_error(self, connection_context, op_id, error, error_type=None):
        if error_type is None:
            error_type = GQL_ERROR

        assert error_type in [GQL_CONNECTION_ERROR, GQL_ERROR], (
            "error_type should be one of the allowed error messages"
            " GQL_CONNECTION_ERROR or GQL_ERROR"
        )

        error_payload = {"message": str(error)}

        return self.send_message(connection_context, op_id, error_type, error_payload)

    def on_message(self, connection_context, message):
        try:
            if not isinstance(message, dict):
                parsed_message = json.loads(message)
                assert isinstance(parsed_message, dict), "Payload must be an object."
            else:
                parsed_message = message
        except Exception as e:
            return self.send_error(connection_context, None, e)

        return self.process_message(connection_context, parsed_message)
