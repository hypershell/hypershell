
#pragma once

#include "hysh/interface/list.h"

hy_declare_interface(hy_list_builder);
hy_declare_interface(hy_list_builder_factory);

hy_define_interface(hy_list_builder, hy_object)
    hy_error (*append_item)(void *self, hy_object item, hy_iid object_iid);
    
    hy_error (*freeze_list)(void *self, hy_list *retval);
hy_end

hy_define_interface(hy_list_builder_factory, hy_object)
    hy_error (*create_list_builder)(void *self, hy_list_builder *retval);

    hy_error (*create_typed_list_builder)(void *self, hy_iid iid, hy_list_builder *retval);
hy_end