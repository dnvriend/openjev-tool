"""Completion command for openjev-tool.

Note: This code was generated with assistance from AI coding tools
and has been reviewed and tested by a human.
"""

import typer
import typer.completion as typer_completion

from openjev_tool.app import make_app

completion_app = make_app("Generate shell completion scripts.")


@completion_app.command(name="generate")
def generate_completion(
    shell: typer_completion.Shells = typer.Argument(
        ...,
        help="Shell type (bash, zsh, fish, powershell, pwsh)",
    ),
) -> None:
    """Generate shell completion script.

    Install instructions:

    \b
    # Bash (add to ~/.bashrc):
    eval "$(openjev-tool completion generate bash)"

    \b
    # Zsh (add to ~/.zshrc):
    eval "$(openjev-tool completion generate zsh)"

    \b
    # Fish (add to ~/.config/fish/completions/openjev-tool.fish):
    openjev-tool completion generate fish > ~/.config/fish/completions/openjev-tool.fish

    \b
    File-based Installation (Recommended for better performance):

    \b
    # Bash
    openjev-tool completion generate bash > ~/.openjev-tool-complete.bash
    echo 'source ~/.openjev-tool-complete.bash' >> ~/.bashrc

    \b
    # Zsh
    openjev-tool completion generate zsh > ~/.openjev-tool-complete.zsh
    echo 'source ~/.openjev-tool-complete.zsh' >> ~/.zshrc

    \b
    Supported Shells: bash, zsh, fish, powershell, pwsh.
    """
    script = typer_completion.get_completion_script(
        prog_name="openjev-tool",
        complete_var="_OPENJEV_TOOL_COMPLETE",
        shell=shell.value,  # nosec B604 — shell is a shell-type string, not subprocess shell=True
    )
    typer.echo(script)
