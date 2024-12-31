# Shell completion definitions (BASH/ZSH, POSIX/Linux)


function _hs() {

	local current="${COMP_WORDS[COMP_CWORD]}"
	local all_opts="initdb config task submit client server cluster -h --help -v --version --citation"

	local i=1 cmd=
	while [[ "${i}" -lt "${COMP_CWORD}" ]]
	do
		local opt="${COMP_WORDS[i]}"
		case "${opt}" in
			-*) ;;
			*)
				cmd="${opt}"
				break
				;;
		esac
		(( i++ ))
	done

	if [[ "${i}" -eq "${COMP_CWORD}" ]]
	then
		COMPREPLY=($(compgen -W "${all_opts}" -- "${current}"))
		return
	fi

	case "${cmd}" in
		initdb)  _hs_initdb  ;;
		config)  _hs_config  ;;
		task)    _hs_task    ;;
		submit)  _hs_submit  ;;
		server)  _hs_server  ;;
		client)  _hs_client  ;;
		cluster) _hs_cluster ;;
		*)                   ;;
	esac
}

function _hs_initdb ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	COMPREPLY=($(compgen -W "-h --help -y --yes -t --truncate" -- "${current}"))
}


function _hs_config ()
{
	local all_opts="get set edit which -h --help"

	local i=1 opt= subcmd_i=
	while [[ ${i} -lt ${COMP_CWORD} ]]; do
		opt="${COMP_WORDS[i]}"
		case "${opt}" in
			get | set | edit | which)
				subcmd_i=${i}
				break
				;;
		esac
		(( i++ ))
	done

	local cmd="${COMP_WORDS[subcmd_i]}"
	case "${cmd}" in
		get)
			_hs_config_get
			return
			;;
		set)
			_hs_config_set
			return
			;;
		edit)
			_hs_config_edit
			return
			;;
		which)
			_hs_config_which
			return
			;;
	esac

	local current="${COMP_WORDS[COMP_CWORD]}"
	COMPREPLY=($(compgen -W "${all_opts}" -- "$current"))
}


function _hs_config_get ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local all_opts="-h --help -x --expand -r --raw --user --system --local --default"

	case "${COMP_CWORD}" in
		3)
			COMPREPLY=($(compgen -W ". `hs config get --list-available` ${all_opts}" -- "${current}"))
			;;
		*)
			COMPREPLY=($(compgen -W "${all_opts}" -- "${current}"))
			;;
	esac
}


function _hs_config_set()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"

	case "${COMP_CWORD}" in
		3)
			COMPREPLY=($(compgen -W "`hs config get --list-available` -h --help" -- "${current}"))
			;;
		4)
			case "${previous}" in
				logging.level)
					COMPREPLY=($(compgen -W "trace debug info warning error critical" -- "${current}"))
					;;
				logging.style)
					COMPREPLY=($(compgen -W "default detailed detailed-compact system" -- "${current}"))
					;;
				database.provider)
					COMPREPLY=($(compgen -W "sqlite postgres" -- "${current}"))
					;;
				server.bind)
					COMPREPLY=($(compgen -W "localhost 127.0.0.1 0.0.0.0" -- "${current}"))
					;;
        server.auth)
          # Generate random 16-digit key
          local authkey=$(cat /dev/urandom | head -c 1024 | md5sum | head -c 16 | tr '[:lower:]' '[:upper:]')
          COMPREPLY=($(compgen -W "${authkey}" -- "${current}"))
          ;;
				autoscale.policy)
					COMPREPLY=($(compgen -W "fixed dynamic" -- "${current}"))
					;;
				console.theme)
					COMPREPLY=($(compgen -W "`hs config get --list-console-themes`" -- "${current}"))
					;;
				*)
					case "`hs config which ${previous} --scope`" in
						user)   scope="--user"   ;;
						system) scope="--system" ;;
						local)  scope="--local" ;;
						*)      scope=           ;;
					esac
					COMPREPLY=("'`hs config get ${previous} --raw 2>/dev/null`' ${scope}")
					;;
			esac
			;;
		*)
			COMPREPLY=($(compgen -W "--user --system --local" -- "${current}"))
			;;
	esac
}


function _hs_config_which ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"

	case "${COMP_CWORD}" in
		3)
			COMPREPLY=($(compgen -W ". `hs config get --list-available` -h --help --site" -- "${current}"))
			;;
		*)
			COMPREPLY=($(compgen -W "-h --help --site" -- "${current}"))
			;;
	esac
}


function _hs_config_edit ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	COMPREPLY=($(compgen -W "-h --help --user --system --local" -- "${current}"))
}


function _hs_task ()
{
	local i=1 opt= subcmd_i=
	while [[ ${i} -lt ${COMP_CWORD} ]]; do
		opt="${COMP_WORDS[i]}"
		case "${opt}" in
			submit | info | wait | run | search | update)
				subcmd_i=${i}
				break
				;;
		esac
		(( i++ ))
	done

	local cmd="${COMP_WORDS[subcmd_i]}"
	case "${cmd}" in
		submit)
			_hs_task_submit
			return
			;;
		info)
			_hs_task_info
			return
			;;
		wait)
			_hs_task_wait
			return
			;;
		run)
			_hs_task_run
			return
			;;
		search)
			_hs_task_search
			return
			;;
		update)
			_hs_task_update
			return
			;;
	esac

	local current="${COMP_WORDS[COMP_CWORD]}"
	COMPREPLY=($(compgen -W "submit info wait run update search -h --help" -- "$current"))
}


function _hs_task_submit ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"

	local i=1 opt= active_collector=none

	while [[ ${i} -lt ${COMP_CWORD} ]]; do
		opt="${COMP_WORDS[i]}"
		case "${opt}" in
			--)            active_collector=--   ; break;;
			-t | --tag)    active_collector=tag         ;;
			-*)            active_collector=none        ;;
		esac
		(( i++ ))
	done

	case "${active_collector}" in
		--)
			COMPREPLY=()
			return
			;;
		tag)
			case "${current}" in
				*:)
					COMPREPLY=($(compgen -W "`hs task search --tag-values ${current::-1} 2>/dev/null`" -- "${current}"))
					;;
				*)
					COMPREPLY=($(compgen -W "`hs task search --tag-keys 2>/dev/null`" -- "${current}"))
					;;
			esac
			return
			;;
	esac

	COMPREPLY=($(compgen -W "-h --help -t --tag --" -- "${current}"))
}


# Limit applied to completion search of database
export _HS_TASK_SEARCH_LIMIT=100


function _hs_task_info ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"

	case "${COMP_CWORD}" in
		3)
			COMPREPLY=($(compgen -W "`hs task search id -l ${_HS_TASK_SEARCH_LIMIT} 2>/dev/null`" -- "${current}"))
			;;
		*)
			case "${previous}" in
				-f | --format)
					COMPREPLY=($(compgen -W "normal yaml json" -- "${current}"))
					;;
				-x | --extract)
					COMPREPLY=($(compgen -W "`hs task --list-columns 2>/dev/null`" -- "${current}"))
					;;
				*)
					COMPREPLY=($(compgen -W "-h --help -f --format --yaml --json -x --extract --stdout --stderr" -- "${current}"))
					;;
			esac
			;;
	esac
}


function _hs_task_wait ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"

	case "${COMP_CWORD}" in
		3)
			COMPREPLY=($(compgen -W "`hs task search id -l ${_HS_TASK_SEARCH_LIMIT} 2>/dev/null`" -- "${current}"))
			;;
		*)
			case "${previous}" in
				-n | --interval)
					COMPREPLY=($(compgen -W "`seq 9`" -- "${current}"))
					;;
				-f | --format)
					COMPREPLY=($(compgen -W "normal json yaml" -- "${current}"))
					;;
				*)
					COMPREPLY=($(compgen -W "-h --help -f --format --yaml --json -s --status -r --return" -- "${current}"))
					;;
			esac
			;;
	esac
}


function _hs_task_run ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"

	local i=1 opt= active_collector=none

	while [[ ${i} -lt ${COMP_CWORD} ]]; do
		opt="${COMP_WORDS[i]}"
		case "${opt}" in
			--)            active_collector=--   ; break;;
			-t | --tag)    active_collector=tag         ;;
			-*)            active_collector=none        ;;
		esac
		(( i++ ))
	done

	case "${active_collector}" in
		--)
			COMPREPLY=()
			return
			;;
		tag)
			case "${current}" in
				*:)
					COMPREPLY=($(compgen -W "`hs task search --tag-values ${current::-1} 2>/dev/null`" -- "${current}"))
					;;
				*)
					COMPREPLY=($(compgen -W "`hs task search --tag-keys 2>/dev/null`" -- "${current}"))
					;;
			esac
			return
			;;
	esac

	case "${previous}" in
		-n | --interval)
			COMPREPLY=($(compgen -W "10 30 60" -- "${current}"))
			return
			;;
	esac

	COMPREPLY=($(compgen -W "-h --help -t --tag -n --interval --" -- "${current}"))
}


function _hs_task_search ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"

	local i=1 opt= active_collector=none

	while [[ ${i} -lt ${COMP_CWORD} ]]; do
		opt="${COMP_WORDS[i]}"
		case "${opt}" in
			--)            active_collector=--;   break;;
			-t | --tag)    active_collector=tag   ;;
			-w | --where)  active_collector=where ;;
			-*)            active_collector=none  ;;
		esac
		(( i++ ))
	done

	local all_opts="-h --help -w --where -t --with-tag -s --order-by --desc -F --failed -C
	--completed -S --succeeded -R --remaining -f --format --csv --json -d --delimiter
	-l --limit -c --count --"

	case "${active_collector}" in
		-- | where)
			COMPREPLY=($(compgen -W "`hs task --list-columns 2>/dev/null`" -- "${current}"))
			return
			;;
		tag)
			case "${current}" in
				*:)
					#echo "`hs task search --tag-values ${current::-1} 2>/dev/null`" 1>&2
					COMPREPLY=($(compgen -W "`hs task search --tag-values ${current::-1} 2>/dev/null`" -- "${current}"))
					return
					;;
				*)
					COMPREPLY=($(compgen -W "`hs task search --tag-keys 2>/dev/null`" -- "${current}"))
					return
					;;
			esac
			;;
	esac

	case "${previous}" in
		-s | --order-by)
			COMPREPLY=($(compgen -W "`hs task --list-columns 2>/dev/null`" -- "${current}"))
			return
			;;
		-f | --format)
			COMPREPLY=($(compgen -W "normal plain table csv json" -- "${current}"))
			return
			;;
		-d | --delimiter)
			COMPREPLY=($(compgen -W '"," ";" "|"' -- "${current}"))
			return
			;;
		-l | --limit)
			COMPREPLY=($(compgen -W "10 20 30 40 50 100" -- "${current}"))
			return
			;;
	esac

	COMPREPLY=($(compgen -W "`hs task --list-columns 2>/dev/null` ${all_opts}" -- "${current}"))
}


function _hs_task_update ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"

	local i=1 opt= active_collector=none

	while [[ ${i} -lt ${COMP_CWORD} ]]; do
		opt="${COMP_WORDS[i]}"
		case "${opt}" in
			--)            active_collector=--;     break;;
			-t | --tag)    active_collector=tag          ;;
			--remove-tag)  active_collector=remove-tag   ;;
			-w | --where)  active_collector=where        ;;
			-*)            active_collector=none         ;;
		esac
		(( i++ ))
	done

	local all_opts="-h --help -w --where -t --with-tag -s --order-by --desc
	--cancel --revert --delete --remove-tag -F --failed -C
	--completed -S --succeeded -R --remaining -l --limit -f --no-confirm --"

	local pos_args=
	for opt in $(hs task --list-columns 2>/dev/null)
	do
		case "${opt}" in
			id) ;;  # We do not allow modifying the 'id' field
			*) pos_args="${pos_args} ${opt}=";;
		esac
	done
	for tag in $(hs task search --tag-keys 2>/dev/null)
	do
		pos_args="${pos_args} ${tag}:"
	done

	case "${active_collector}" in
		--)
		    COMPREPLY=($(compgen -W "${pos_args}" -- "${current}"))
		    return
		    ;;

		where)
			COMPREPLY=($(compgen -W "`hs task --list-columns 2>/dev/null`" -- "${current}"))
			return
			;;
		tag | remove-tag)
			case "${current}" in
				*:)
					COMPREPLY=($(compgen -W "`hs task search --tag-values ${current::-1} 2>/dev/null`" -- "${current}"))
					return
					;;
				*)
					COMPREPLY=($(compgen -W "`hs task search --tag-keys 2>/dev/null`" -- "${current}"))
					return
					;;
			esac
			;;
	esac

	case "${previous}" in
		-s | --order-by)
			COMPREPLY=($(compgen -W "`hs task --list-columns 2>/dev/null`" -- "${current}"))
			return
			;;
		-l | --limit)
			COMPREPLY=($(compgen -W "1 10 100 1000" -- "${current}"))
			return
			;;
	esac

	COMPREPLY=($(compgen -W "${pos_args} ${all_opts}" -- "${current}"))
}


function _hs_submit ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"
	local all_opts="-h --help -t --template --tag -b --bundlesize -w --bundlewait --init-db --"

	if [ "${COMP_CWORD}" -eq 2 ]
	then
		case "${current}" in
			-*)
				COMPREPLY=($(compgen -W "${all_opts}" -- "${current}"))
				;;
			*)
				COMPREPLY=($(compgen -f -- "${current}"))
				;;
		esac
		return
	fi

	local i=1 opt= active_collector=none

	while [[ ${i} -lt ${COMP_CWORD} ]]; do
		opt="${COMP_WORDS[i]}"
		case "${opt}" in
			--)       active_collector=--   ; break;;
			--tag)    active_collector=tag         ;;
			-*)       active_collector=none        ;;
		esac
		(( i++ ))
	done

	case "${active_collector}" in
		--)
			COMPREPLY=($(compgen -f -- "${current}"))
			return
			;;
		tag)
			case "${current}" in
				*:) COMPREPLY=($(compgen -W "`hs task search --tag-values ${current::-1} 2>/dev/null`" -- "${current}")) ;;
				*)  COMPREPLY=($(compgen -W "`hs task search --tag-keys 2>/dev/null`" -- "${current}")) ;;
			esac
			return
			;;
	esac

	case "${previous}" in
		-b | --bundlesize)
			COMPREPLY=($(compgen -W "1 10 100 1000" -- "${current}"))
			return
			;;
		-w | --bundlewait)
			COMPREPLY=($(compgen -W "10 30 60 600" -- "${current}"))
			return
			;;
		-t | --template)
			COMPREPLY=()  # No completion available for command templates
			return
			;;
	esac

	COMPREPLY=($(compgen -W "${all_opts}" -- "${current}"))
}


function _hs_client ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"
	local all_opts="-h --help -N --num-tasks -t --template -b --bundlesize -w --bundlewait
	-H --host -p --port -k --auth -d --delay-start --no-confirm -o --output -e --errors -c --capture
	-T --timeout -W --task-timeout"

	case "${previous}" in
		-N | --num-tasks)
			COMPREPLY=($(compgen -W "1 $(seq 2 2 `hs client --available-cores`)" -- "${current}"))
			return
			;;
		-b | --bundlesize)
			COMPREPLY=($(compgen -W "1 10 100 1000" -- "${current}"))
			return
			;;
		-w | --bundlewait)
			COMPREPLY=($(compgen -W "10 60 600" -- "${current}"))
			return
			;;
		-t | --template)
			COMPREPLY=()  # No completion available for command templates
			return
			;;
		-H | --host)
			COMPREPLY=($(compgen -W "`_hs_known_hosts`" -- "${current}"))
			return
			;;
		-p | --port)
			# Just populate using the config (default if not configured)
			# Probably not what the client needs but at least it shows an integer
			COMPREPLY=($(compgen -W "`hs config get server.port --raw 2>/dev/null`" -- "${current}"))
			return
			;;
		-k | --auth)
			# We cannot know what the server-side key is so just populate from configuration
			COMPREPLY=($(compgen -W "`hs config get server.auth --raw 2>/dev/null`" -- "${current}"))
			return
			;;
		-d | --delay-start)
			COMPREPLY=($(compgen -W "30 60 600 -60 -600" -- "${current}"))
			return
			;;
		-o | --output | -e | --errors)
			COMPREPLY=($(compgen -f -- "${current}"))
			return
			;;
		-T | --timeout | -W | --task-timeout)
			COMPREPLY=($(compgen -W "10 30 60 600" -- "${current}"))
			return
			;;
	esac

	COMPREPLY=($(compgen -W "${all_opts}" -- "${current}"))
}


function _hs_known_hosts()
{
	(\
		echo "localhost"; \
		echo "127.0.0.1"; \
		echo "0.0.0.0"; \
		cat ~/.ssh/known_hosts; \
		cat /etc/hosts \
	) 2>/dev/null \
		| grep -E '^[0-9a-zA-Z]' \
		| awk '{print $1}' \
		| sort -u
}


function _hs_server ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"
	local all_opts="-h --help --forever --restart -H --bind -p --port -k --auth
	--no-db --no-confirm --initdb -f --failures --print
	-b --bundlesize -w --bundlewait -r --max-retries --eager"

	if [ "${COMP_CWORD}" -eq 2 ]
	then
		case "${current}" in
			-*)
				COMPREPLY=($(compgen -W "${all_opts}" -- "${current}"))
				;;
			*)
				COMPREPLY=($(compgen -f -- "${current}"))
				;;
		esac
		return
	fi

	case "${previous}" in
		-b | --bundlesize)
			COMPREPLY=($(compgen -W "1 10 100 1000" -- "${current}"))
			return
			;;
		-w | --bundlewait)
			COMPREPLY=($(compgen -W "10 60 600" -- "${current}"))
			return
			;;
		-H | --bind)
			COMPREPLY=($(compgen -W "localhost 127.0.0.1 0.0.0.0" -- "${current}"))
			return
			;;
		-p | --port)
			COMPREPLY=($(compgen -W "`hs server --available-ports 2>/dev/null`" -- "${current}"))
			return
			;;
		-k | --auth)
			# Generate random 16-digit key
			local authkey=$(cat /dev/urandom | head -c 1024 | md5sum | head -c 16 | tr '[:lower:]' '[:upper:]')
			COMPREPLY=($(compgen -W "${authkey}" -- "${current}"))
			return
			;;
		-f | --failures)
			COMPREPLY=($(compgen -f -- "${current}"))
			return
			;;
		-r | --max-retries)
			COMPREPLY=($(compgen -W "1 2 3 4 5 6" -- "${current}"))
			return
			;;
	esac

	COMPREPLY=($(compgen -W "${all_opts}" -- "${current}"))
}


function _hs_cluster ()
{
	local current="${COMP_WORDS[COMP_CWORD]}"
	local previous="${COMP_WORDS[COMP_CWORD - 1]}"
	local all_opts="-N --num-tasks -t --template -p --port -b --bundlesize -w --bundlewait
	-r --max-retries --eager --no-db --initdb --no-confirm --forever --restart --ssh-args --ssh-group
	-E --env --remote-exe -d --delay-start -c --capture -o --output -e --errors -f --failures
	-T --timeout -W --task-timeout -A --autoscaling -F --factor -P --period -I --init-size
	-X --min-size -Y --max-size -h --help"

	if [ "${COMP_CWORD}" -eq 2 ]
	then
		case "${current}" in
			-*)
				COMPREPLY=($(compgen -W "${all_opts}" -- "${current}"))
				;;
			*)
				COMPREPLY=($(compgen -f -- "${current}"))
				;;
		esac
		return
	fi

	local i=1 opt= active_collector=none

	while [[ ${i} -lt ${COMP_CWORD} ]]; do
		opt="${COMP_WORDS[i]}"
		case "${opt}" in
			--)       active_collector=--   ; break;;
			-*)       active_collector=none        ;;
		esac
		(( i++ ))
	done

	case "${active_collector}" in
		--)
			COMPREPLY=($(compgen -f -- "${current}"))
			return
			;;
	esac

	case "${previous}" in
		-N | --num-tasks)
			COMPREPLY=($(compgen -W "1 $(seq 2 2 `hs client --available-cores`)" -- "${current}"))
			return
			;;
		-t | --template | --ssh-args | --remote-exe)
			COMPREPLY=()  # No completion available for command templates or ssh options
			return
			;;
		-p | --port)
			COMPREPLY=($(compgen -W "`hs server --available-ports 2>/dev/null`" -- "${current}"))
			return
			;;
		--ssh-group)
			COMPREPLY=($(compgen -W "`hs client --available-ssh-groups 2>/dev/null`" -- "${current}"))
			return
			;;
		-b | --bundlesize)
			COMPREPLY=($(compgen -W "1 10 100 1000" -- "${current}"))
			return
			;;
		-w | --bundlewait)
			COMPREPLY=($(compgen -W "10 60 600" -- "${current}"))
			return
			;;
		-r | --max-retries)
			COMPREPLY=($(compgen -W "1 2 3 4 5 6" -- "${current}"))
			return
			;;
		-d | --delay-start)
			COMPREPLY=($(compgen -W "30 60 600 -60 -600" -- "${current}"))
			return
			;;
		-o | --output | -e | --errors | -f | --failures)
			COMPREPLY=($(compgen -f -- "${current}"))
			return
			;;
		-T | --timeout | -W | --task-timeout)
			COMPREPLY=($(compgen -W "10 30 60 600" -- "${current}"))
			return
			;;
		-A | --autoscaling)
			COMPREPLY=($(compgen -W "fixed dynamic" -- "${current}"))
			return
			;;
		-F | --factor)
			COMPREPLY=($(compgen -W "1 2 3 4 5" -- "${current}"))
			return
			;;
		-P | --period)
			COMPREPLY=($(compgen -W "30 60 300 600" -- "${current}"))
			return
			;;
		-I | --init-size | -X | --min-size)
			COMPREPLY=($(compgen -W "0 1" -- "${current}"))
			return
			;;
		-Y | --max-size)
			COMPREPLY=($(compgen -W "1 2 5" -- "${current}"))
			return
			;;
	esac

	COMPREPLY=($(compgen -W "${all_opts}" -- "${current}"))
}


complete -o bashdefault -F _hs hs
complete -o bashdefault -F _hs hyper-shell
