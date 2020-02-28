#compdef redtime

_redtime_command() {
    local redtime_commands=("${(@f)$(redtime complete)}")
    _describe -t 'commands' 'redtime command' redtime_commands
}

# "${(@f)...}"
#    ^^^^
# http://zsh.sourceforge.net/Doc/Release/Expansion.html#Parameter-Expansion-Flags

_redtime_subcommand() {
  # echo "$curcontext" "|" "${context[@]}" "|" "${words[@]}" "|" "$CURRENT" >> /tmp/comp-debug
  # echo "cmd: redtime complete --nth $CURRENT -- ${words[@]}" >> /tmp/comp-debug

  local redtime_arguments  # using local with assignment supresses status code
  redtime_arguments=("${(@f)$(redtime complete --nth $CURRENT -- ${words[@]})}")
  if (( $? == 0 )); then
    _describe 'redtime argument' redtime_arguments
  fi

  local redtime_options
  redtime_options=("${(@f)$(redtime complete --options -- ${words[1]})}")
  if (( $? == 0 )); then
    _describe -o 'redtime options' redtime_options
  fi
}

_arguments \
  ':command:_redtime_command' \
  '*::subcommand:_redtime_subcommand' \
  '--help:Show help'

# http://zsh.sourceforge.net/Guide/zshguide06.html