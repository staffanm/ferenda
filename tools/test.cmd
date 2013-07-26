@ECHO OFF
IF [%1] == [] (
   python -Wi -m unittest discover -v test
) ELSE (
  SET PYTHONPATH=test
  python -Wi -m unittest -v %1
)