
#pragma once

#include "hysh/wrapper/Object.hpp"

class Error :
    public Object
{
  public:
    hy_error* ErrorCode(hy_error_code *retval) {
        return Methods()->error_code(Self(), retval);
    }
    
    hy_error* ErrorMessage(const char **retval) {
        return Methods()->error_message(Self(), retval);
    }
}