
#pragma once

#include "hysh/interface/string.h"
#include "hysh/interface/list.h"

hy_declare_interface(hy_error_string);
hy_declare_interface(hy_traceback_error);
hy_declare_interface(hy_traceback_item);

hy_define_interface(hy_error_string, hy_error)
    hy_error (*error_string)(void *self, hy_string *retval);
hy_end

hy_define_interface(hy_traceback_error, hy_error_string)
    hy_error (*traceback)(void *self, hy_list *retval);
hy_end

hy_define_interface(hy_traceback_item, hy_object)
    hy_error (*line_number)(void *self, uint64_t *retval);
    
    hy_error (*source_file)(void *self, hy_string *retval);
hy_end