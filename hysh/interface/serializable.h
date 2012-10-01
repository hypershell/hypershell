
#pragma once

#include "hysh/interface/string.h"

hy_declare_interface(hy_serializable);

hy_define_interface(hy_serializable, hy_object)
    hy_error* (*to_string)(void *self, hy_string **retval);
hy_end

hy_define_interface(hy_serializable_converter, hy_object)
    hy_error* (*serializable_to_string_proxy)(void *self, 
        hy_serializable *serializable,
        hy_string **retval);
hy_end