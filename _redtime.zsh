#compdef redtime


_redtime_command() {
    local redtime_commands=("${(@f)$(redtime complete)}")
    _describe -t 'commands' 'redtime command' redtime_commands
}

_redtime_subcommand() {
  # echo "$curcontext" "|" "${context[@]}" "|" "${words[@]}" "|" "$CURRENT"
  local redtime_arguments=("${(@f)$(redtime complete --nth $CURRENT -- ${words[@]})}")
  local redtime_options=("${(@f)$(redtime complete --options -- ${words[1]})}")

  _describe 'redtime argument' redtime_arguments
  _describe -o 'redtime options' redtime_options
}

_arguments \
  ':command:_redtime_command' \
  '*::subcommand:_redtime_subcommand' \
  '--help:Show help'

# http://zsh.sourceforge.net/Guide/zshguide06.html