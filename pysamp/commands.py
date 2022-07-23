import functools
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from samp import SendClientMessage  # type: ignore

from .callbacks import registry


class ArgumentConversionError(ValueError):
    def __init__(self, arg_type, arg_name, arg_input):
        self.arg_type = arg_type
        self.arg_name = arg_name
        self.arg_input = arg_input


class CommandHandler(Protocol):
    def __call__(self, playerid: int, *args: str) -> None: ...


class Validator(Protocol):
    """Return True if playerid is allowed, False otherwise."""
    def __call__(self, playerid: int) -> bool: ...


class Message(Protocol):
    text: str
    color: int

    def send(self, playerid: int) -> None: ...


@dataclass
class Command:
    triggers: set[str]
    handler: CommandHandler
    requires: tuple[Validator, ...]
    error_message: Message

    def __post_init__(self):
        parameters = list(inspect.signature(self.handler).parameters.values())
        self._min_params = len([
            parameter
            for parameter in parameters
            if parameter.default is inspect._empty
            and parameter.kind != inspect.Parameter.VAR_POSITIONAL
        ])
        self._max_params = len(parameters) if not any(
            parameter.kind == inspect.Parameter.VAR_POSITIONAL
            for parameter in parameters
        ) else 1e3
        self._parameters = parameters
        self._usage_message = BaseMessage(
            text=f'USAGE: {list(self.triggers)[0]} ' + ' '.join(
                parameter.name
                if parameter.default is inspect._empty
                and parameter.kind != inspect.Parameter.VAR_POSITIONAL
                else f'[{parameter.name}]'
                for parameter in parameters[1:]
            ),
            color=0xFF0000FF,
        )

    def handle(self, playerid: int, args_text: str) -> None:
        """Call handler, doing validation and argument conversion."""
        for validator in self.requires:
            if not validator(playerid):
                self.error_message.send(playerid)
                return True

        args = [playerid] + [arg for arg in args_text.split(' ') if arg]

        if not (self._min_params <= len(args) <= self._max_params):
            self._usage_message.send(playerid)
            return True

        try:
            self.handler(*self._convert_args(args))
        except ArgumentConversionError as exception:
            BaseMessage(
                text=(
                    f'ERROR: Invalid {exception.arg_type} '
                    f'for argument {exception.arg_name}: '
                    f'"{exception.arg_input}"'
                ),
                color=0xFF0000FF,
            ).send(playerid)

        return True

    def _convert_args(self, args):
        new_args = []

        for index, (arg, parameter) in enumerate(zip(args, self._parameters)):
            if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                new_args.extend(args[index:])
                break

            annotation = parameter.annotation

            if annotation is not inspect._empty:
                try:
                    arg = annotation(arg)
                except (ValueError, TypeError):
                    raise ArgumentConversionError(
                        arg_type=annotation.__name__,
                        arg_name=parameter.name,
                        arg_input=arg,
                    )

            new_args.append(arg)

        return new_args


@dataclass
class CommandDispatcher:
    _commands: list[Command] = field(default_factory=list)
    _commands_by_trigger: dict[str, Command] = field(default_factory=dict)

    def _register(self, command: Command) -> None:
        """Register a Command to be triggered later on.

        Don't use this directly: prefer the @cmd decorator which does extra
        checks in addition to looking much better.
        """
        self._commands.append(command)
        self._commands_by_trigger.update({
            trigger: command
            for trigger in command.triggers
        })

    def handle(self, playerid: int, command_text: str) -> bool:
        """Attempt to handle command_text sent by playerid.

        Returns True if a Command was found, False otherwise.
        Should be used in OnPlayerCommandText.
        """
        trigger, _, args_text = command_text.partition(' ')
        command = self._commands_by_trigger.get(trigger)

        if not command:
            return False

        command.handle(playerid, args_text)
        return True


@dataclass
class BaseMessage:
    """Bare message class implementing Message protocol."""
    text: str
    color: int

    def send(self, playerid: int) -> None:
        SendClientMessage(playerid, self.color, self.text)


def _NO_FUNCTION(playerid: int, *args: str) -> None: ...


DEFAULT_ERROR_MESSAGE = BaseMessage(
    text='You are not allowed to use this command.',
    color=0xFF0000FF,
)


def cmd(
    function: CommandHandler = _NO_FUNCTION,
    /,
    *,
    aliases: tuple[str, ...] = (),
    use_function_name: bool = True,
    requires: tuple[Validator, ...] = (),
    error_message: Message = DEFAULT_ERROR_MESSAGE,
) -> Callable[[Any], Any]:
    """Decorate a command handler to register it with the given options.

    function: The command handler to register. Useful when there is no need for
        other arguments, one can use the bare decorator without calling it.
    aliases: Alternative command names to trigger the handler with. If this is
        empty and use_function_name is False, a ValueError is raised.
    use_function_name: Whether to use the function name as a command name to
        trigger the handler with. If this is False and aliases is empty, a
        ValueError is raised.
    requires: Tuple of callables implementing the Validator protocol. If
        specified, they will be called in order with a playerid as argument
        and should return False if the player is not allowed to use this
        command, in which case error_message will be issued.
    error_message: An object implementing the Message protocol. It will be sent
        to the player in case they are not allowed to use this command.
    """
    if function is _NO_FUNCTION:
        return functools.partial(
            cmd,
            aliases=aliases,
            use_function_name=use_function_name,
            requires=requires,
            error_message=error_message,
        )

    triggers = set()

    if use_function_name:
        # See https://github.com/python/mypy/issues/12795
        triggers.add(function.__name__)  # type: ignore

    triggers.update(aliases)

    if not triggers:
        raise ValueError('Unable to register a command without triggers.')

    dispatcher._register(Command(
        triggers={f'/{trigger}' for trigger in triggers},
        handler=function,
        requires=requires,
        error_message=error_message,
    ))

    return function


dispatcher = CommandDispatcher()
registry.register_callback(
    'OnPlayerCommandText',
    dispatcher.handle,
    'pysamp.commands',
)
