
#pragma once

hy_declare_interface(hy_basic_callback);
hy_declare_interface(hy_basic_writer);

hy_define_interface(hy_basic_callback, hy_object)
    hy_error* (*on_error)(void *self, hy_error *error);
hy_end

hy_define_interface(hy_basic_writer, hy_object)
    hy_error* (*write_error)(void *self, hy_error *error);
hy_end