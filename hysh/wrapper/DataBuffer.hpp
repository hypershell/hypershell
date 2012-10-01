
#pragma once

namespace hysh {

class DataBuffer :
    public Object
{
  public:
    DataBufferPtr(hy_data_buffer *ptr) :
        Object((hy_object*) ptr)
    { }

    hyresult Size(uint64_t *retval) {
        return Ptr()->size(Self(), retval);
    }

    hyresult Buffer(const char **retval) {
        return Ptr()->buffer(Self(), retval);
    }

    hy_data_buffer* Ptr() {
        return (hy_data_buffer*) Object::Ptr();
    }

    hy_data_buffer** EditPtr() {
        return (hy_data_buffer**) Object::EditPtr();
    }

    operator hy_data_buffer*() {
        return Ptr();
    }
};

}