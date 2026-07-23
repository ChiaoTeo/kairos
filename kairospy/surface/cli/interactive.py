from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path


MainCallable = Callable[[list[str] | None], int]
WorkspaceChoicesCallable = Callable[[], tuple[str, ...]]


def interactive_shell(
    main_func: MainCallable,
    *,
    workspace_choices_func: WorkspaceChoicesCallable | None = None,
) -> int:
    session = InteractiveSession(
        main_func,
        workspace_choices_func=workspace_choices_func or interactive_workspace_choices,
    )
    print(session.banner(), flush=True)
    print(session.menu(), flush=True)
    while True:
        try:
            line = input(session.prompt())
        except EOFError:
            print("", flush=True)
            return 0
        except KeyboardInterrupt:
            print("\nUse `quit` to exit.", flush=True)
            continue
        line = line.strip()
        if not line:
            continue
        try:
            should_exit = session.handle(line)
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 2
            if code:
                print(f"Command exited with status {code}", flush=True)
            should_exit = False
        except (KeyError, LookupError, PermissionError, ValueError, FileNotFoundError) as error:
            print(f"Command failed: {error}", flush=True)
            should_exit = False
        if should_exit:
            return 0


class InteractiveSession:
    def __init__(
        self,
        main_func: MainCallable,
        *,
        workspace_choices_func: WorkspaceChoicesCallable = lambda: (),
    ) -> None:
        self._main = main_func
        self._workspace_choices_func = workspace_choices_func
        self.mode = "top"
        self.workspace_name: str | None = None
        self.workspace_choices: tuple[str, ...] = ()

    def banner(self) -> str:
        return "Kairos interactive shell. Type a number, a command, `back`, `help`, or `quit`."

    def prompt(self) -> str:
        if self.mode == "top":
            return "kairos> "
        if self.mode == "workspace" and self.workspace_name:
            return f"kairos/workspace[{self.workspace_name}]> "
        return f"kairos/{self.mode}> "

    def menu(self) -> str:
        if self.mode == "top":
            return "\n".join([
                "1  run",
                "2  workspace",
                "3  data",
                "4  config",
                "q  quit",
            ])
        if self.mode == "run":
            return "\n".join([
                "run mode",
                "1  live <run-id>        enter a live-run control console",
                "2  start --config <path>",
                "3  status --run-id <id>",
                "b  back",
            ])
        if self.mode == "workspace":
            if self.workspace_name:
                return "\n".join([
                    f"workspace {self.workspace_name}",
                    "1  inspect",
                    "2  bind --name <name> --dataset <dataset>",
                    "b  back",
                ])
            return "\n".join([
                "workspace mode",
                "0 <name>  create workspace",
                "<number>  enter listed workspace",
                "list      refresh workspace list",
                "b  back",
            ])
        if self.mode == "data":
            return "\n".join([
                "data mode",
                "1  list",
                "2  describe --dataset <dataset>",
                "3  doctor --dataset <dataset>",
                "b  back",
            ])
        if self.mode == "config":
            return "\n".join([
                "config mode",
                "1  show",
                "2  set <path> <value>",
                "3  validate",
                "b  back",
            ])
        return ""

    def handle(self, line: str) -> bool:
        parts = shlex.split(line)
        if not parts:
            return False
        command = parts[0]
        if command in {"quit", "exit", "q"}:
            return True
        if command in {"back", "b", ".."}:
            if self.mode == "workspace" and self.workspace_name:
                self.workspace_name = None
            else:
                self.mode = "top"
                self.workspace_name = None
            print(self.menu(), flush=True)
            return False
        if command in {"help", "?", "menu"}:
            print(self.menu(), flush=True)
            return False
        if self.mode == "top":
            return self._handle_top(parts)
        if self.mode == "run":
            return self._handle_group("run", parts)
        if self.mode == "workspace":
            return self._handle_group("workspace", parts)
        if self.mode == "data":
            return self._handle_group("data", parts)
        if self.mode == "config":
            return self._handle_group("config", parts)
        self._main(parts)
        return False

    def _handle_top(self, parts: list[str]) -> bool:
        command = parts[0]
        mode_by_command = {
            "1": "run",
            "run": "run",
            "2": "workspace",
            "workspace": "workspace",
            "3": "data",
            "data": "data",
            "4": "config",
            "config": "config",
        }
        if command in mode_by_command and len(parts) == 1:
            self.mode = mode_by_command[command]
            if self.mode == "workspace":
                self._show_workspace_selector()
            else:
                print(self.menu(), flush=True)
            return False
        if command == "live":
            self._enter_live(parts[1:])
            return False
        self._main(parts)
        return False

    def _handle_group(self, group: str, parts: list[str]) -> bool:
        command = parts[0]
        if group == "run":
            if command == "1":
                print("Usage: live <run-id>", flush=True)
                return False
            if command == "2":
                print("Usage: start --config <path> [--confirm-live]", flush=True)
                return False
            if command == "3":
                print("Usage: status --run-id <id>", flush=True)
                return False
            if command == "live":
                self._enter_live(parts[1:])
                return False
        if group == "workspace":
            if self.workspace_name:
                return self._handle_workspace_context(parts)
            if command == "0":
                self._create_workspace(parts[1:])
                return False
            if command.isdigit() and self.workspace_choices:
                selected = self._workspace_choice(command)
                if selected is not None:
                    self._enter_workspace([selected])
                    return False
            if command == "list":
                self._show_workspace_selector()
                return False
            if command in {"open", "select", "use"}:
                self._enter_workspace(parts[1:])
                return False
            if command == "create":
                self._create_workspace(parts[1:])
                return False
            if command not in {"workspace"}:
                self._enter_workspace(parts)
                return False
        elif group == "data":
            parts = interactive_expand_number(parts, {
                "1": "list",
                "2": "describe",
                "3": "doctor",
            })
        elif group == "config":
            parts = interactive_expand_number(parts, {
                "1": "show",
                "2": "set",
                "3": "validate",
            })
        elif group == "run":
            parts = interactive_expand_number(parts, {
                "2": "start",
                "3": "status",
            })
        if parts[0] == group:
            self._main(parts)
        else:
            self._main([group, *parts])
        return False

    def _show_workspace_selector(self) -> None:
        self._main(["workspace", "list"])
        self.workspace_choices = self._workspace_choices_func()
        print(interactive_workspace_choice_hint(self.workspace_choices), flush=True)

    def _create_workspace(self, parts: list[str]) -> None:
        if not parts:
            print("Usage: 0 <workspace-name>", flush=True)
            return
        name = parts[0]
        self._main(["workspace", "create", name])
        self.workspace_choices = self._workspace_choices_func()
        self._enter_workspace([name])

    def _handle_workspace_context(self, parts: list[str]) -> bool:
        if self.workspace_name is None:
            return False
        parts = interactive_expand_number(parts, {
            "1": "inspect",
            "2": "bind",
        })
        command = parts[0]
        if command == "inspect":
            self._main(["workspace", "inspect", self.workspace_name, *parts[1:]])
            return False
        if command in {"bind", "attach"}:
            self._main(["workspace", "attach", self.workspace_name, *parts[1:]])
            return False
        if command == "create":
            self._main(["workspace", "create", self.workspace_name, *parts[1:]])
            return False
        if command == "workspace":
            self._main(parts)
            return False
        self._main(["workspace", command, self.workspace_name, *parts[1:]])
        return False

    def _enter_workspace(self, parts: list[str]) -> None:
        if not parts:
            print("Usage: open <workspace-name>", flush=True)
            return
        self.workspace_name = parts[0]
        print(self.menu(), flush=True)

    def _workspace_choice(self, value: str) -> str | None:
        try:
            index = int(value)
        except ValueError:
            return None
        if index < 1 or index > len(self.workspace_choices):
            return None
        return self.workspace_choices[index - 1]

    def _enter_live(self, parts: list[str]) -> None:
        if not parts:
            print("Usage: live <run-id>", flush=True)
            return
        run_id = parts[0]
        extra = parts[1:]
        self._main([
            "run",
            "live",
            "attach",
            "--run-id",
            run_id,
            *extra,
        ])


def interactive_expand_number(parts: list[str], mapping: dict[str, str]) -> list[str]:
    if parts and parts[0] in mapping:
        return [mapping[parts[0]], *parts[1:]]
    return parts


def interactive_workspace_choices() -> tuple[str, ...]:
    try:
        from kairospy.workspace import WorkspaceRepository

        repository = WorkspaceRepository.discover(Path.cwd())
        return tuple(workspace.name for workspace in repository.list())
    except Exception:
        return ()


def interactive_workspace_choice_hint(choices: tuple[str, ...]) -> str:
    lines = ["Select workspace:"]
    lines.extend(f"  {index}  {name}" for index, name in enumerate(choices, start=1))
    return "\n".join(lines)
