
#pragma once

#include "hysh/interface/data_buffer.h"

hy_declare_interface(hy_string);

hy_define_interface(hy_string, hy_data_buffer)
    hy_error (*length)(void *self, uint64_t *retval);
    
    hy_error (*c_string)(void *self, const char **retval);
    
hy_end_define