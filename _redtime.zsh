#compdef redtime


_redtime_command() {
    local redtime_commands=("${(@f)$(redtime complete)}")
    _describe -t 'redtime-commands' 'redtime command' redtime_commands
}

_redtime_subcommand() {
  # echo "$curcontext" "|" "${context[@]}" "|" "${words[@]}" "|" "$CURRENT"
  local redtime_arguments=("${(@f)$(redtime complete ${words[@]} --nth $CURRENT)}")
  local redtime_options=("${(@f)$(redtime complete ${words[@]} --options)}")

  _describe -t 'redtime-arguments' 'redtime argument' redtime_arguments
  _describe -o -t 'redtime-options' 'redtime options' redtime_options
}

_arguments \
  ':command:_redtime_command' \
  '*::subcommand:_redtime_subcommand' \
  '--help:Show help'
