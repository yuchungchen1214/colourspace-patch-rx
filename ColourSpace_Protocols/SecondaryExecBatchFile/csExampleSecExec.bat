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

set fileName="\Users\%USERNAME%\Desktop\csMeasurmentLogFile.txt"

set /a counter=1
:ARGLBEG
if "%1"=="" goto ARGLEND
set ag%counter%=%1
goto ARGNEXT 

:ARGNEXT
shift
set /a counter=%counter%+1
goto ARGLBEG

:ARGLEND
echo %ag1% %ag2% %ag3% %ag4% %ag5% %ag6% %ag7% %ag8% %ag9% %ag10% %ag11% %ag12% %ag13% %ag14% %ag15% %ag16% %ag17% %ag18% %ag19% %ag20% %ag21% %ag22% %ag23% %ag24% >> %fileName%
goto end

:end

