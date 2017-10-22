#!/bin/sh


# Control how unittest is run. Some examples:
# -f exit at first failure in order to not have to wait three minutes.
# -v verbose progress of tests
UNITTESTOPTS=-v

while getopts ":q" opt; do
  case $opt in
      q)
      # be quiet	  
      UNITTESTOPTS=	
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
PYTHONWARNINGS=-Wi::DeprecationWarning:bs4

if [ -n "$1" ]
then
    PYTHONPATH=test python $PYTHONWARNINGS -m unittest $UNITTESTOPTS "$1"
else
    # When running the entire suite, exit at first failure (-f) in
    # order to not have to wait three minutes.
    python $PYTHONWARNINGS -m unittest discover $UNITTESTOPTS test
    python -V
fi
