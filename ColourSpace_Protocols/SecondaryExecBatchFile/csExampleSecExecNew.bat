@rem //********************************************************************************************
@rem //*
@rem //*         Copyright (c) Light Illusion Ltd. 2020
@rem //*
@rem //*         All rights reserved Used under authorization. This material contains the 
@rem //*         confidential and proprietary information of Light Illusion Ltd
@rem //*         and may not be copied in whole or in part without the express written
@rem //*         permission of Light Illusion Ltd.
@rem //*         This copyright notice does not imply publication.
@rem //*         
@rem //********************************************************************************************

@echo off
setlocal enabledelayedexpansion

set "fileName=\Users\%USERNAME%\Desktop\csMeasurmentLogFile.txt"
set /a counter=1

:ARGLBEG
if "%1"=="" goto ARGLEND
set "ag!counter!=%1"
shift
set /a counter+=1
goto ARGLBEG

:ARGLEND
(for /l %%i in (1,1,%counter%) do echo !ag%%i!) >> %fileName%
endlocal


