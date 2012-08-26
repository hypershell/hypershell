
#pragma once

#include "hysh/interface/string.h"
#include "hysh/interface/list.h"

hy_declare_interface(hy_table);

hy_define_interface(hy_table, hy_object)
    hy_error (*get_value)(void *self, hy_string key, hy_string *retval);
    
    hy_error (*get_keys)(void *self, hy_list *retval);
hy_end