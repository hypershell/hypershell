
#pragma once

#include "hysh/interface/object.h"

hy_declare_interface(hy_data_buffer);

hy_define_interface(hy_data_buffer, hy_object)
    hy_error* (*size)(void *self, uint64_t *retval);
    
    hy_error* (*buffer)(void *self, const char **retval);
hy_end