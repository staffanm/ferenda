#!/bin/sh


# Control how unittest is run. Some examples:
# -f exit at first failure in order to not have to wait three minutes.
# -q quietize
UNITTESTOPTS=-v

while getopts ":qf" opt; do
  case $opt in
      q)
	  # be quiet	  
	  UNITTESTOPTS=	
	  ;;
      f)
	  UNITTESTOPTS="$UNITTESTOPTS -f"
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      ;;
  esac
done
shift $((OPTIND-1))


# Control how warnings are processed. Some examples:
# -We::UserWarning to make exceptions out of warnings
# -Wi::DeprecationWarning:bs4 to ignore warnings in the bs4 module
PYTHONWARNINGS=

if [ $# -gt 0 ]
then
    PYTHONPATH=test python $PYTHONWARNINGS -m unittest $UNITTESTOPTS $@
else
    python $PYTHONWARNINGS -m unittest discover $UNITTESTOPTS test
    python -V
fi
