#encoding: utf-8

""" 
This module makes this distribution of programs easier by automatically
creating or activating a virtualenv as needed when a program starts.
Just add these lines to the top of your program:

    import autovenv
    autovenv.run()
"""


__version__ = '0.2'


import os
import sys


def log(marker, *strings, **kw):
    '''
    [+], [-] and [warning] output with ANSI color escapes (where available)
    '''
    fmt = '\033[3{color}m[{marker}]\033[0m {string}' if sys.stdout.isatty() else '[{marker}] {string}'
    color = {'-':'1', '+':'2', 'i':'4', 'error':'1', 'success':'2'}.get(marker[0], '3')
    string = ' '.join(str(x) for x in strings)
    print(fmt.format(**locals()))
    if 'error' in kw and kw['error']: 
        sys.exit(int(kw['error']))


if os.environ.get('SERVER_SOFTWARE') or float('%s.%s' %(sys.version_info.major, sys.version_info.minor)) < 3.3:

    def run(**kw):
        log('i',  "Autovenv won't run in this environment. Please set up a virtualenv manually.")

else:

    import inspect
    import shutil
    import subprocess
    import venv

    def run(venv_name='venv', requirements_file='requirements.txt'):
        # Detect flags intended for autovenv and remove them from sys.argv
        flags = '--no-autovenv --remove-venv'
        flags = {f:not sys.argv.remove(f) if f in sys.argv else False for f in flags.split()}

        # Do nothing if this is the second stage run where an environment 
        # variable has already been set, or if the user disabled autovenv
        if flags['--no-autovenv'] or 'AUTOVENV_IS_RUNNING' in os.environ:
            return
        os.environ['AUTOVENV_IS_RUNNING'] = __version__

        # Find the __main__ module which called this function and look for
        # or create a virtualenv in that module's containing directory
        caller = [f[1] for f in inspect.stack() if f[0].f_locals.get('__name__') == '__main__'][0]
        calling_script = os.path.realpath(caller)
        calling_script_dir = os.path.dirname(calling_script)
        venv_dir = os.path.join(calling_script_dir, venv_name)
        venv_python = os.path.join(venv_dir, 'bin', 'python')

        # Show the disclaimer
        log('*',  "Autovenv is bootstrapping a virtual environment using " + requirements_file,
            "\n      --no-autovenv      Don't auto activate or install a virtualenv",
            "\n      --remove-venv      Remove old virtualenv so a fresh one can be installed",
            "\n")
        log('+', 'Running', calling_script, '\n   ', len('Running '+calling_script)*'-')

        # Remove the bad virtualenv
        if flags['--remove-venv']:
            shutil.rmtree(venv_dir, ignore_errors=True)
            log('i', 'Removed existing virtualenv', error=1)

        # Handle the case of the nonexistant virtualenv by creating it
        if not os.path.isfile(venv_python):
            log('i', 'No virtualenv found')

            # Run with working directory of the calling script
            original_working_dir = os.getcwd()
            os.chdir(calling_script_dir)
            log('i', 'Changed working directory to', calling_script_dir)
            
            # Create the virtualenv
            venv.create(venv_dir, with_pip=True)
            log('+', 'Created virtualenv', venv_dir)

            # Call pip to install the requirements file
            log('i', 'Installing required packages (this may take some time)')
            if subprocess.call([venv_python, "-m", "pip", "install", "-r", requirements_file]):
                # A nonzero return code means something went wrong
                log('-', 'Installing required packages failed!')
                shutil.rmtree(venv_dir)
                log('i', 'Removed the incomplete or broken virtualenv in', venv_dir)
                log('darn', 'sucks. is your', requirements_file, 'file any good?'); 
                log('+google?', 'do you maybe need a "-dev" package or some compiler?', error=1);
            log('+', 'Installation was successful!')

            # Return to the original working directory
            os.chdir(original_working_dir)
            log('i', 'Restored working directory to', os.getcwd())

        # If it exists, we assume the whole thing works; no warranties
        log('i', 'Found virtualenv', venv_dir)

        # Pass the python path twice to convince its silly little brain
        os.execl(venv_python, venv_python, calling_script, *sys.argv[1:])


#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# autovenv.py is a python version of the following hybrid bash/python program:
#------------------------------>8----[cut-here]-------------------------------
#!/bin/bash
#encoding:utf8
''':'
cd "$(cd $(dirname $0) && pwd)" # Set working directory to that of the program
if [ ! -f venv/bin/activate ]; then 
    echo "No virtualenv found - creating one..."
    python3 -m venv venv
    ln -fs venv/bin/activate
    . ./activate
    echo "Installing modules to virtualenv"
    if ! pip install -r requirements.txt; then
        echo "Removing broken virtualenv"
        rm -fr activate venv
        exit 1
    fi
    echo -n "Setup complete! "
fi
echo "Activating virtualenv"
. ./activate
python $(basename $0)
exit
':'''
#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# From here onward python syntax is used. Importantly, bash is the hashbang
# executable which must first run this program. A syntax sleight-of-hand is
# used to create code which looks to bash like cancelling-out quotes '' and 
# quoted noop instructions ':', and looks to python like a multiline string.
# The exit command prevents bash from attempting to execute any further.
# Python assigns the multiline string to __doc__, which, if you want one, can
# be overwritten with a real docstring as simply as __doc__ = '''...''' 
#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

